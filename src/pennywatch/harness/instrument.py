"""Turning Cachegrind's counting off and on from Python.

`import catalyst` varies by about a million instructions from run to run, in two distinct modes, for
a reason not yet found. So imports aren't counted and subtracted: they aren't counted at all. Each
process starts out counting, which is what lets Catalyst's compiler, a child process, be counted,
and the harness stops that before its first import.
"""

import ctypes

_library = ctypes.CDLL("/opt/pennywatch/libinstrument.so")

stop = _library.pennywatch_stop
start = _library.pennywatch_start
