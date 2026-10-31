/* For test_gdb.py: C that calls back into Scheme, and a place to stop. */
int stop_here(int x) {
  volatile int r = x;
  return r;
}

int call_it(int (*f)(int), int x) {
  volatile int r = f(x);
  return r + 1;
}
