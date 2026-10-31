#lang racket/base
(require rackunit racket/list "../sampler.rkt")

(define stacks
  (parse-folded
   (string-append
    "call-in-empty-metacontinuation-frame_[i];namespace-require+_[i];[anonymous]_[j];"
    "call-with-values_[i];[...base/nbody.rkt:156:0_[j];advance@nbody.rkt:119_[j] 7\n"
    "main@prog.rkt:3_[j];dynamic-wind_[i];[anonymous]_[i];hash-ref_[i] 2\n"
    "[C]_start;[C]S_vfasl 1\n")))

(check-equal? (map cdr stacks) '(7 2 1))
(check-equal? (car (car (parse-folded "]!draw_method_in_canvas%@unit.rkt:12_[j] 1")))
              (list (frame "draw_method_in_canvas%" 'program "unit.rkt" 12)))
(check-equal? (first (car (first stacks)))
              (frame "call-in-empty-metacontinuation-frame" 'runtime #f #f))
(check-equal? (last (car (first stacks))) (frame "advance" 'program "nbody.rkt" 119))
(check-equal? (list-ref (car (first stacks)) 4) (frame #f 'program "nbody.rkt" 156))

(define tidied (tidy-stacks stacks))
;; start-up and glue gone; the module body is named after what it runs
(check-equal? (map frame-name (car (first tidied))) '("[λ → nbody.rkt]" #f "advance"))
;; dynamic-wind and the anonymous runtime thunk between program code and a
;; runtime leaf are dropped; the leaf stays
(check-equal? (map frame-name (car (second tidied))) '("main" "hash-ref"))
;; a stack with no program frame is left alone
(check-equal? (car (third tidied)) (car (third stacks)))

(define out (open-output-string))
(write-folded (list (second tidied)) out)
(check-equal? (get-output-string out) "main@prog.rkt:3_[j];hash-ref_[i] 2\n")

(when (sampler-available?)
  (define (spin n) (if (zero? n) 0 (+ 1 (spin (sub1 n)))))
  (sampler-take!)
  (sampler-start!)
  (for ([i 200]) (spin 100000))
  (sampler-stop!)
  (check-true (pair? (sampler-take!))))
