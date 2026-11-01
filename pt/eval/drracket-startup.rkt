#lang racket/base
;; Start DrRacket and exit once its first frame is shown; prints the startup time
;; (ms since this module started) to stderr.
(require racket/gui/base
         racket/class)
(define t0 (current-inexact-milliseconds))
(void (thread (λ ()
                (let loop ()
                  (sleep 0.02)
                  (if (for/or ([f (in-list (parameterize ([current-eventspace (current-eventspace)])
                                             (get-top-level-windows)))])
                        (send f is-shown?))
                      (begin
                        (eprintf "drracket-startup-ms ~a\n"
                                 (round (- (current-inexact-milliseconds) t0)))
                        (exit 0))
                      (loop))))))
(dynamic-require 'drracket #f)
(yield (make-semaphore))
