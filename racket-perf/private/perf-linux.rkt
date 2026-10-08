#lang racket/base
;; Linux process operations. posix_spawn avoids running Racket in a forked,
;; multithreaded child and keeps traced children out of subprocess's reaper.
(require ffi/unsafe racket/list)
(provide spawn-executable ptrace wait-child kill-child make-control control-write
         control-read close-control)
(define libc (ffi-lib #f))
(define spawn
  (get-ffi-obj "posix_spawn" libc
    (_fun (pid : (_ptr o _int)) _bytes/nul-terminated _pointer _pointer
          (_list i _bytes/nul-terminated) (_list i _bytes/nul-terminated) -> (result : _int)
          -> (values result pid))))
(define (spawn-executable command)
  (define env (current-environment-variables))
  (define-values (result pid)
    (spawn (string->bytes/utf-8 (car command)) #f #f
           (append (map string->bytes/utf-8 command) '(#f))
           (append (for/list ([key (in-list (environment-variables-names env))])
                     (bytes-append key #"=" (environment-variables-ref env key))) '(#f))))
  (unless (zero? result) (error 'posix_spawn "error ~a launching ~a" result (car command)))
  pid)
(define raw-ptrace (get-ffi-obj "ptrace" libc (_fun #:save-errno 'posix _uint _int _pointer _uintptr -> _long)))
(define (ptrace request pid [data 0])
  (when (= -1 (raw-ptrace request pid #f data))
    (error 'ptrace "request ~a for process ~a: errno ~a" request pid (saved-errno))))
(define waitpid
  (get-ffi-obj "waitpid" libc
    (_fun #:save-errno 'posix _int (status : (_ptr o _int)) _int
          -> (pid : _int) -> (values pid status))))
(define (wait-child pid [poll? #f])
  (define-values (result status) (waitpid pid (if poll? 1 0)))
  (when (= result -1) (error 'waitpid "process ~a: errno ~a" pid (saved-errno)))
  (and (positive? result) status))
(define raw-kill (get-ffi-obj "kill" libc (_fun _int _int -> _int)))
(define (kill-child pid signal) (raw-kill pid signal) (void))
(define mkfifo (get-ffi-obj "mkfifo" libc (_fun _path _uint -> _int)))
(define open (get-ffi-obj "open" libc (_fun _path _int -> _int)))
(define close-control (get-ffi-obj "close" libc (_fun _int -> _int)))
(define write (get-ffi-obj "write" libc (_fun _int _bytes _size -> _ssize)))
(define read (get-ffi-obj "read" libc (_fun _int _bytes _size -> _ssize)))
(define (make-control path)
  (unless (zero? (mkfifo path #o600)) (error 'mkfifo "cannot create ~a" path))
  (define fd (open path (bitwise-ior 2 #x800 #x80000))) ; RDWR, NONBLOCK, CLOEXEC
  (when (negative? fd) (error 'open "cannot open ~a" path))
  fd)
(define (control-write fd command)
  (define data (string->bytes/utf-8 (string-append command "\n")))
  (unless (= (write fd data (bytes-length data)) (bytes-length data))
    (error 'perf "cannot send ~a" command)))
(define (control-read fd)
  (define data (make-bytes 128))
  (define n (read fd data 128))
  (and (positive? n) (subbytes data 0 n)))
