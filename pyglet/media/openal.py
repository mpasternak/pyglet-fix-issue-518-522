#!/usr/bin/python
# $Id$

import ctypes
import time

from pyglet.media import Sound, Listener
from pyglet.media import lib_openal as al
from pyglet.media import lib_alc as alc

def init():
    device = alc.alcOpenDevice(None)
    if not device:
        raise Exception('No OpenAL device.')

    alcontext = alc.alcCreateContext(device, None)
    alc.alcMakeContextCurrent(alcontext)

class BufferPool(list):
    def __init__(self):
        self.timestamps = {}

    def get(self, timestamp):
        if not self:
            buffer = al.ALuint()
            al.alGenBuffers(1, buffer)
        else:
            buffer = al.ALuint(self.pop(0))
        self.timestamps[buffer.value] = timestamp
        return buffer

    def timestamp(self, buffer):
        return self.timestamps[buffer]

    def replace(self, buffers):
        self.extend(buffers)

    def __del__(self, al=al):
        if al and al.ALuint and al.alDeleteBuffers:
            buffers = (al.ALuint * len(self))(*self)
            al.alDeleteBuffers(len(self), buffers)

buffer_pool = BufferPool()

_format_map = {
    (1,  8): al.AL_FORMAT_MONO8,
    (1, 16): al.AL_FORMAT_MONO16,
    (2,  8): al.AL_FORMAT_STEREO8,
    (2, 16): al.AL_FORMAT_STEREO16,
}
def get_format(channels, depth):
    return _format_map[channels, depth]

class OpenALSound(Sound):
    _processed_buffers = 0
    _queued_buffers = 0

    def __init__(self):
        self.source = al.ALuint()
        al.alGenSources(1, self.source)
        self.play_when_buffered = False

        # OpenAL on Linux lacks the time functions, so this is a stab at
        # interpolating time between the known buffer timestamps.  When a
        # timestamp is read from the active buffer, the current system time is
        # stored in self._last_buffer_time, and the buffer that was used is
        # stored in self._last_buffer. 
        #
        # If the same buffer is in use the next time the time is requested,
        # the elapsed system time is added to the buffer timestamp.
        #
        # The (completely invalid) assumption is that the buffer is just
        # beginning when _last_buffer_time is stored.  This is more likely
        # than not, at least, if the buffers are long.  If the buffers are
        # short, hopefully not much interpolation will be needed and the
        # difference won't be noticeable.
        #
        # The alternative -- reusing the same timestamp without adding system
        # time -- results in slightly jumpy video due to lost frames.
        #
        # XXX Mac OS X should implement the time functions, so no need for
        # this hackery.  Need to detect AL 1.1.
        #
        # XXX Need special consideration when pausing.
        self._last_buffer = 0
        self._last_buffer_time = 0

    def __del__(self, al=al):
        if al and al.alDeleteSources:
            al.alDeleteSources(1, self.source)

    def _get_time(self):
        buffer = al.ALint()
        al.alGetSourcei(self.source, al.AL_BUFFER, buffer)
        if not buffer:
            return 0.

        buffer_timestamp = buffer_pool.timestamp(buffer.value)
        if buffer.value == self._last_buffer:
            return time.time() - self._last_buffer_time + buffer_timestamp
        else:
            self._last_buffer = buffer.value
            self._last_buffer_time = time.time()
            return buffer_timestamp

    def play(self):
        self._openal_play()

    def _openal_play(self):
        if self.playing:
            return

        buffers = al.ALint()
        al.alGetSourcei(self.source, al.AL_BUFFERS_QUEUED, buffers)
        if buffers.value == 0:
            self.play_when_buffered = True
        else:
            al.alSourcePlay(self.source)
            self.play_when_buffered = False
            self.playing = True

    def pause(self):
        self.playing = False
        al.alSourcePause(self.source)

    def _set_volume(self, volume):
        al.alSourcef(self.source, al.AL_GAIN, max(0, volume))
        self._volume = volume

    def _set_min_gain(self, min_gain):
        al.alSourcef(self.source, al.AL_MIN_GAIN, max(0, min_gain))
        self._min_gain = min_gain

    def _set_max_gain(self, max_gain):
        al.alSourcef(self.source, al.AL_MAX_GAIN, max(0, max_gain))
        self._max_gain = max_gain

    def _set_position(self, position):
        x, y, z = position
        al.alSource3f(self.source, al.AL_POSITION, x, y, z)
        self._position = position

    def _set_velocity(self, velocity):
        x, y, z = velocity
        al.alSource3f(self.source, al.AL_VELOCITY, x, y, z)
        self._velocity = velocity

    def _set_pitch(self, pitch):
        al.alSourcef(self.source, al.AL_PITCH, max(0, pitch))
        self._pitch = pitch

    def _set_cone_orientation(self, cone_orientation):
        x, y, z = cone_orientation
        al.alSource3f(self.source, al.AL_DIRECTION, x, y, z)
        self._cone_orientation = cone_orientation

    def _set_cone_inner_angle(self, cone_inner_angle):
        al.alSourcef(self.source, al.AL_CONE_INNER_ANGLE, cone_inner_angle)
        self._cone_inner_angle = cone_inner_angle

    def _set_cone_outer_angle(self, cone_outer_angle):
        al.alSourcef(self.source, al.AL_CONE_OUTER_ANGLE, cone_outer_angle)
        self._cone_outer_angle = cone_outer_angle

    def _set_cone_outer_gain(self, cone_outer_gain):
        al.alSourcef(self.source, al.AL_CONE_OUTER_GAIN, cone_outer_gain)
        self._cone_outer_gain = cone_outer_gain

    def dispatch_events(self):
        queued = al.ALint()
        processed = al.ALint()
        al.alGetSourcei(self.source, al.AL_BUFFERS_QUEUED, queued)
        al.alGetSourcei(self.source, al.AL_BUFFERS_PROCESSED, processed)
        if processed.value == queued.value:
            self.finished = True
            self.playing = False
        self._processed_buffers = processed.value
        self._queued_buffers = queued.value

        if self.play_when_buffered and queued.value:
            self._openal_play()

class OpenALStreamingSound(OpenALSound):
    def dispatch_events(self):
        super(OpenALStreamingSound, self).dispatch_events()

        # Release spent buffers
        if self._processed_buffers:
            discard_buffers = (al.ALuint * self._processed_buffers)()
            al.alSourceUnqueueBuffers(
                self.source, len(discard_buffers), discard_buffers)
            buffer_pool.replace(discard_buffers)

class OpenALStaticSound(OpenALSound):
    def __init__(self, medium):
        super(OpenALStaticSound, self).__init__()

        # Keep a reference to the medium to avoid premature release of
        # buffers.
        self.medium = medium

class OpenALListener(Listener):
    def _set_position(self, position):
        x, y, z = position
        al.alListener3f(al.AL_POSITION, x, y, z)
        self._position = position 

    def _set_velocity(self, velocity):
        x, y, z = velocity
        al.alListener3f(al.AL_VELOCITY, x, y, z)
        self._velocity = velocity 

    def _set_forward_orientation(self, orientation):
        val = (ALfloat * 6)(*(orientation + self._up_orientation))
        al.alListenerfv(al.AL_ORIENTATION, val)
        self._forward_orientation = orientation

    def _set_up_orientation(self, orientation):
        val = (ALfloat * 6)(*(self._forward_orientation + orientation))
        al.alListenerfv(al.AL_ORIENTATION, val)
        self._up_orientation = orientation

    def _set_doppler_factor(self, factor):
        al.alDopplerFactor(factor)
        self._doppler_factor = factor

    def _set_speed_of_sound(self, speed_of_sound):
        al.alSpeedOfSound(speed_of_sound)
        self._speed_of_sound = speed_of_sound
