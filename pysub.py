# -*- encoding:utf-8 -*-

'''
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

«Copyright 2012 Salvador de la Puente & Daniel Martín»
'''

'''
This module contains the class SubtitleStream to load SubRip [1] format. See the
class for extended documentation but basically you can load a SubRip (.srt) file
shift it backward / forward, edit the text or check for sequence integrity.

Instances of SubtitleStream act like sequences so they are iterable and you can
get slices of it.

You can save() the stream as well.
'''

import sys
import re
try:
    from pyraphrase import get_paraphrases
except ImportError:
    get_paraphrases = None
from os import path
import datetime
from types import StringType, ListType

__all__ = ['SRTParseException', 'Fragment', 'SubtitleStream']

def normalize(line):
    '''
    Remove punctuation marks and possibly other innecesary symbols from lines that are noise
    for our experiments.
    '''
    simplification_table = {
        u'á': u'a',
        u'é': u'e',
        u'í': u'i',
        u'ó': u'o',
        u'ú': u'u',
        u'ü': u'u'
    }

    # Lower the case
    line = line.lower()

    # Remove tildes
    line = ''.join([(simplification_table.get(c, False) or c) for c in line])

    # Ignore HTML markers and other punctuation symbols
    line = re.sub(ur'<.+?>|[^\wñ]+', ' ', line)

    return line

def _u(t):
    '''
    Returns the number of total microseconds in a time object counting all
    components: hours, minutes, seconds and microseconds.
    '''
    return t.hour*60*60*1000000\
            + t.minute*60*1000000\
            + t.second*1000000\
            + t.microsecond

class SRTParseException(BaseException):
    '''Represents some problem occured while parsing a SRT subtitle file'''
    def __init__(self, *args, **kwargs):
        BaseException.__init__(self, *args, **kwargs)

class Fragment(object):
    '''
    Represents some subtitled interaction such as a dialog, a situation text,
    background ambient sound...

    Fragments have a sequence number indicating its relative order inside the
    film. Both starttime and endtime bounding the period during the interaction
    is happening and one or more lines of text. It is possible to access to the
    duration of a fragment too.
    '''

    _time_format = u'%H:%M:%S,%f'

    def __init__(self, seqnumber, starttime, endtime, textlines):
        self._seqnumber = seqnumber
        self._starttime = starttime
        self._endtime = endtime
        self._textlines = textlines

    def __unicode__(self):
        formatted_starttime = unicode(self._starttime.strftime(self._time_format))[:-3]
        formatted_endtime = unicode(self._endtime.strftime(self._time_format))[:-3]
        output = u'%d\n%s --> %s\n%s' % (self._seqnumber,
                                        formatted_starttime,
                                        formatted_endtime,
                                        u'\n'.join(self._textlines))
        return output

    def __str__(self):
        return self.__unicode__().encode('utf-8')

    def shift(self, microseconds):
        '''
        Returns a new Fragment shifted as many microseconds as indicated as the
        parameter. To shift backward use a negative amount.
        '''
        delta = datetime.timedelta(microseconds=abs(microseconds))
        if microseconds < 0:
            return Fragment(self._seqnumber,
                             self._starttime - delta,
                             self._endtime - delta,
                             self._textlines)
        else:
            return Fragment(self._seqnumber,
                             self._starttime + delta,
                             self._endtime + delta,
                             self._textlines)

    @property
    def duration(self):
        endmcs = _u(self._endtime)
        startmcs = _u(self._starttime)
        return  endmcs - startmcs

    @property
    def seqnumber(self):
        return self._seqnumber

    @property
    def starttime(self):
        return self._starttime

    @property
    def endtime(self):
        return self._endtime

    @property
    def textlines(self):
        return self._textlines

    @property
    def text(self):
        return u'\n'.join(self.textlines)

    @text.setter
    def text(self, value):
        try:
            assert(isinstance(value, basestring))
            self._textlines = value.split(u'\n')
        except AssertionError:
            raise TypeError('text must be an instance of basestring such as str or unicode')


class SubtitleStream(object):
    '''
    Instances of this class represent subtitle streams as a sequence of
    Fragments. They act as sequence type and they support indexation, iteration
    and slicing.

    Other operations are supported as well such as:
        synchronization between two streams
        shifting forward or backward some microseconds

    Use save(path [, encoding=None]) method providing a path and an optional
    encoding to save the stream.
    '''

    @staticmethod
    def synchronize(sstream1, sstream2, checkpoints_count=1):
        '''
        Returns a list of pairs of synchronized fragments taking as reference
        the shortest one of them.
        '''
        if not get_paraphrases:
            raise Exception('pyraphrase module should be in the PYTHONPATH to use this functionallity')

        def build_delay_function(totaldelay, duration):
            return lambda x : float(_u(x.starttime)) * totaldelay / duration

        # Partial synchronization
        totaldelay = _u(sstream1[-1].starttime) - _u(sstream2[-1].starttime)
        reference, delayed = (sstream1, sstream2) if totaldelay < 0 else (sstream2, sstream1)
        duration = _u(reference[-1].starttime)

        f = build_delay_function(abs(totaldelay), duration)
        return SubtitleStream._synchronize(reference, delayed, f)

    @staticmethod
    def _synchronize(reference, delayed, d):
        '''
        Actual implementation of the synchronizing algorithm. It tries to predict
        the amount of delay for each reference fragment so it fixes the delayed
        stream an look for the nearest fragment to the current reference.

        It takes in count the reference stream (the shortest one), the delayed
        one (the largest) and the delay predicion function (currently a linear
        interpolation between 0 and the max delay between streams assuming the
        error as accumulative.)
        '''
        def select_closest(ref, options):
            best_choice = options[0]
            best_confidence = 0
            interval = float(_u(options[-1].starttime) - _u(options[0].starttime))
            for f in options:
                _, common, stats = get_paraphrases(normalize(ref.text), normalize(f.text))

                # Confidence calculation based on longest common subsequence ratio,
                # time duration
                # and overlap
                lcsr = min(stats['lcsr'])
                lcsr_score = lcsr

                time = abs(_u(ref.starttime) - _u(f.starttime))
                time_score = 1-time/interval

                overlap = min(_u(ref.endtime), _u(f.endtime)) - max(_u(ref.starttime), _u(f.starttime))
                overlap_score = overlap/float(ref.duration) if overlap > 0 else 0

                # TODO: Justify this or adapt it via CBR
                confidence = .5*time_score + .3*lcsr_score + .2*overlap_score

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_choice = f

            return best_choice, best_confidence

        if not len(reference):
            return []

        result = []
        for fragment in reference:
            # Find nearest fragments
            j = 0
            expecteddelay = d(fragment)
            fixed = delayed.shift(-expecteddelay)
            while j < len(fixed)-1 and fixed[j].starttime < fragment.starttime:
                j += 1

            candidates = list(fixed[max(0,j-1):min(len(fixed),j+2)])

            closest, confidence = select_closest(fragment, candidates)
            result += [(fragment, closest, confidence)]

        # TODO: Group fragments
        return result

    @staticmethod
    def _parse(srtpath, encoding):
        '''
        Parse utility to build a subtitle from a .srt file.
        '''

        stage = 0
        ignoring = True
        fragmentlist = []
        import codecs
        with codecs.open(srtpath, 'rb', encoding=encoding) as srtfile:
            for line in srtfile:
                line = line.strip()
                if not line:
                    if not ignoring:
                        fragmentlist.append(Fragment(seqnumber,
                                                     starttime,
                                                     endtime,
                                                     textlines))
                        stage = 0
                        ignoring = True
                    continue

                else:
                    ignoring = False

                # Subtitle sequence number
                if 0 == stage:
                    try:
                        seqnumber = int(line)
                    except ValueError:
                        raise SRTParseException(u"'%s' is not a valid sequence number" % line)

                # Starttime and endtime
                elif 1 == stage:
                    period = [t.strip() for t in line.split(u'-->')]
                    try:
                        assert(len(period) == 2)
                        starttuple = [int(i) for i in re.split(ur'[:,]', period[0])]
                        assert(len(starttuple) == 4)
                        starttuple[-1] *= 1000
                        starttime = datetime.datetime(1900,1,1, *starttuple)

                        endtuple = [int(i) for i in re.split(ur'[:,]', period[1])]
                        assert(len(endtuple) == 4)
                        endtuple[-1] *= 1000
                        endtime = datetime.datetime(1900,1,1, *endtuple)
                    except AssertionError:
                        raise SRTParseException(u"'%s' does not seem to have a valid period line format" % line)

                # First textline
                elif 2 == stage:
                    try:
                        assert(line)
                        textlines = [line]
                    except AssertionError:
                        raise SRTParseException(u"Dialog lines not found")

                # Rest of the textlines
                else:
                    textlines += [line]

                stage += 1

        if not ignoring:
            fragmentlist.append(Fragment(seqnumber,
                                         starttime,
                                         endtime,
                                         textlines))

        return fragmentlist

    def __init__(self, feed, name=None, encoding='iso8859_15'):
        '''
        Takes a path to the subtitle file and parses it to obtain a sequence of
        fragments or uses a list of fragments to build a SubtitleStream. It
        optionally accepts a name for the stream.
        '''
        self._encoding = encoding
        if type(feed) == StringType:
            self._name = name or path.basename(feed)
            self._fragments = SubtitleStream._parse(feed, encoding)
        elif type(feed) == ListType:
            self._name = name or ''
            try:
                for f in feed:
                    assert(isinstance(f, Fragment))
            except AssertionError:
                raise Exception(u'If input parameter is a list. It should be a list of Fragments (all of them)')

            self._fragments = feed

    def __unicode__(self):
        output = u''
        for fragment in self._fragments:
            output += unicode(fragment)+u'\n\n'
        return output

    def __str__(self):
        return self.__unicode__().encode('utf-8')

    def __repr__(self):
        return u"<SubtitleStream object '%s' with %d fragment(s)>" % (self._name, len(self))

    def __len__(self):
        '''Number of fragments inside the stream.'''

        return len(self._fragments)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return SubtitleStream(self._fragments[key])

        return self._fragments[key]

    def __setitem__(self, key, value):
        try:
            assert(isinstance(value, Fragment))
            self._fragments[key] = value
        except AssertionError:
            raise TypeError(u'value must be a Fragment instance')

    def save(self, path, encoding=None):
        encoding = encoding or self._encoding
        import codecs
        with codecs.open(path, mode='wb', encoding=encoding) as output:
            output.write(self.__unicode__())

    def check_sequence(self, repair=False):
        '''
        Check if sequence numbers start by 1 and are consecutives. If so,
        returns an empty list. If there is some inconsistence, then returns
        a list of triplets with the inconsistent fragments, their wrong sequence
        number and the proper one.

        If repair optional parameter is set to True, then wrong fragments' sequence
        numbers are fixed.
        '''
        wrong_labeled = []
        for i, f in enumerate(self._fragments, 1):
            if f.seqnumber != i:
                wrong_labeled.append((f, f.seqnumber, i))
                if repair:
                    f.seqnumber = i

        return wrong_labeled

    def all_script(self):
        '''
        Returns all script
        '''
        return u' '.join([f.text for f in self._fragments])

    def shift(self, microseconds):
        '''
        Returns a new stream shifted as many microseconds as indicated as the
        parameter. To shift backward use a negative amount.
        '''
        newfragmentlist = []
        for f in self._fragments:
            newfragmentlist.append(f.shift(microseconds))

        return SubtitleStream(newfragmentlist)

    def shift_to_zero(self):
        return self.shift(-_u(self[0].starttime))

    @property
    def duration(self):
        '''
        Returns the amount of microseconds from the begining of the film to the
        end of the last fragment.
        '''

        last = self[-1]
        return _u(self[-1].endtime)

    @property
    def name(self):
        return self._name

    @property
    def encoding(self):
        return self._encoding
