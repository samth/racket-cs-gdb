#lang racket/base
(require rackunit racket/file "../private/perf-map.rkt")

;; A synthetic heap containing a valid code object, its Unicode name,
;; malformed lookalikes, and another valid anonymous procedure.
(define memory (make-bytes 1024))
(define base #x1000)
(define (put offset n [size 8])
  (bytes-copy! memory offset (integer->integer-bytes n size #f #f)))
(define (read address n)
  (define offset (- address base))
  (and (<= 0 offset) (<= (+ offset n) (bytes-length memory))
       (subbytes memory offset (+ offset n))))
(put 0 #xbe)
(put 8 16)
(put 24 (+ base 511))
(put 512 (+ (arithmetic-shift 3 4) 2))
(for ([c (in-string "λgo")] [offset (in-range 520 532 4)])
  (put offset (+ (arithmetic-shift (char->integer c) 8) #x16) 4))
;; Reject unaligned headers and implausible code lengths.
(put 97 #xbe) (put 105 16)
(put 128 #xbe) (put 136 (expt 2 24))
(put 256 #xbe) (put 264 24) (put 280 6)
(check-equal? (scheme-string read (+ base 511) default-layout) "λgo")
(check-false (scheme-string read (+ base 515) default-layout))
(check-equal? (find-code-objects read (list (cons base (+ base 1024))) default-layout 256)
              (list (list (+ base 64) 16 "λgo") (list (+ base 320) 24 "#f")))

(define temp (make-temporary-file "perf-map-test-~a" 'directory))
(dynamic-wind
 void
 (lambda ()
   (define path (build-path temp "map"))
   (define entries '((8192 16 "#f") (4096 16 "p") (12288 16 "fib\nname")))
   (write-perf-map path entries)
   (check-equal? (file->string path) "1000 10 [p]\n2000 10 [#f]\n3000 10 fib name\n")
   (write-perf-map path entries #t)
   (check-regexp-match #rx"p \\[0x1000\\]" (file->string path))
   (define equates (build-path temp "equates.h"))
   (call-with-output-file equates
     (lambda (out)
       (for ([(key value) (in-hash default-layout)])
         (fprintf out "#define ~a (ptr)0x~x\n" key value))))
   (check-equal? (read-equates equates) (hash-copy default-layout))
   (call-with-output-file equates #:exists 'truncate (lambda (out) (display "#define sfalse 6\n" out)))
   (check-exn #rx"lacks" (lambda () (read-equates equates))))
 (lambda () (delete-directory/files temp)))
