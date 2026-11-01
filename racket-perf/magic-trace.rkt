#lang racket/base
;; Event-driven Intel PT snapshots with magic-trace, for Racket CS built with
;; ChezScheme perf-profiling (9310551 or later).
;;
;; magic-trace keeps the last few milliseconds of control flow in a ring buffer and
;; snapshots it when the program calls magic_trace_stop_indicator, which the Chez
;; kernel provides; the stretch since `start` is marked in the trace.
;;
;;   (require racket-perf/magic-trace)
;;   (with-trace-threshold 50 (handle-request r))   ; snapshot if it takes >= 50 ms
;;   (trace-snapshot!)                               ; snapshot now
;;
;; Run under magic-trace with the jitdump on so Scheme code is named:
;;   CHEZ_JITDUMP=DIR CHEZ_JITDUMP_TSC=1 CHEZ_JITDUMP_LAZY=1 MAGIC_TRACE_CHEZ=1 \
;;     magic-trace run -trigger . -- racket prog.rkt   (racket itself, not a wrapper)
;; Without magic-trace attached, a snapshot is a call to an empty C function (and,
;; with CHEZ_JITDUMP_LAZY=1, a walk of all code to write the jitdump).
;; On a Racket without these entry points the functions do nothing.
(require ffi/unsafe/vm)
(provide trace-tsc
         trace-snapshot!
         call-with-trace-threshold
         with-trace-threshold
         trace-snapshot-available?)

(define-values (tsc snap trace-snapshot-available?)
  (with-handlers ([exn:fail? (lambda (e) (values (lambda () 0) (lambda (s a) (void)) #f))])
    (values (vm-eval '(foreign-procedure "(cs)magic_trace_tsc" () unsigned-64))
            (vm-eval '(foreign-procedure "(cs)magic_trace_snapshot"
                                         (unsigned-64 unsigned-64) void))
            #t)))

;; the timestamp counter, for marking where a snapshot's region starts
(define (trace-tsc) (tsc))

(define (trace-snapshot! [start (tsc)] [arg 0])
  (snap start arg))

;; calls thunk, and takes a snapshot when the call took at least ms milliseconds
(define (call-with-trace-threshold ms thunk #:arg [arg 0])
  (define start (tsc))
  (define t0 (current-inexact-monotonic-milliseconds))
  (call-with-values
   thunk
   (lambda results
     (when (>= (- (current-inexact-monotonic-milliseconds) t0) ms)
       (snap start arg))
     (apply values results))))

(define-syntax-rule (with-trace-threshold ms body ...)
  (call-with-trace-threshold ms (lambda () body ...)))
