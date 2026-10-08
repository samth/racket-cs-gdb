#lang racket/base
;; The posix_spawn child loads this before tracing starts. After TRACEME,
;; exec replaces this runtime with the program being profiled.
(require ffi/unsafe)
(define libc (ffi-lib #f))
(define setsid (get-ffi-obj "setsid" libc (_fun -> _int)))
(define ptrace (get-ffi-obj "ptrace" libc (_fun _uint _int _pointer _pointer -> _long)))
(define execv (get-ffi-obj "execv" libc (_fun _bytes/nul-terminated (_list i _bytes/nul-terminated) -> _int)))
(define command (vector->list (current-command-line-arguments)))
(when (or (negative? (setsid)) (= -1 (ptrace 0 0 #f #f)))
  (eprintf "racket-perf: cannot start tracing\n") (exit 127))
(execv (string->bytes/utf-8 (car command))
       (append (map string->bytes/utf-8 command) '(#f)))
(eprintf "racket-perf: cannot execute ~a\n" (car command))
(exit 127)
