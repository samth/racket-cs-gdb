#lang info

(define collection "racket-perf")
(define deps '("base"))
(define build-deps '("rackunit-lib" "scribble-lib" "racket-doc"))
(define pkg-desc "Named Linux perf profiles for Racket CS programs")
(define version "0.1")
(define pkg-authors '(samth))
(define license '(Apache-2.0 OR MIT))
(define raco-commands
  '(("racket-perf" racket-perf/main
                   "record and report a named Racket CS perf profile" #f)))
(define scribblings '(("scribblings/racket-perf.scrbl" () (tool))))
(define test-omit-paths '("main.rkt" "private/perf-exec.rkt" "bin"))
