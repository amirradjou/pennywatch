/* Lets the harness turn Cachegrind's counting off and on. These are Valgrind client requests: outside
   Valgrind they do nothing. */
#include <valgrind/cachegrind.h>

void pennywatch_stop(void) { CACHEGRIND_STOP_INSTRUMENTATION; }
void pennywatch_start(void) { CACHEGRIND_START_INSTRUMENTATION; }
