#lang racket/base
;; In-process stack sampling for Racket CS, through the sampler built into
;; Chez Scheme (c/sampler.inc). The sampler interrupts the running thread
;; on a CPU-time clock and walks Chez's own frame metadata, so stacks are
;; complete (through continuations and Racket's metacontinuations) without
;; perf, frame pointers, or root access.
(require racket/list racket/string ffi/unsafe/vm
         (only-in profile/analyzer analyze-samples)
         (prefix-in text: profile/render-text))
(provide sampler-available?
         sampler-start! sampler-stop! sampler-take!
         (struct-out frame)
         parse-folded read-folded write-folded
         tidy-stack tidy-stacks
         profile-thunk/sampler profile/sampler
         samples->profile)

;; One frame of a sampled stack. `origin` is 'program for Scheme code that
;; was not loaded from a boot file, 'runtime for Chez and the Racket CS
;; runtime layers (rumble, expander, io, thread), and 'c for C code.
;; `file` and `line` come from the procedure's source information, when it
;; has some.
(struct frame (name origin file line) #:transparent)

(define-values (vm-start vm-stop vm-take)
  (with-handlers ([exn:fail? (lambda (e) (values #f #f #f))])
    (values (vm-eval '(foreign-procedure "(cs)sampler_start" (int) int))
            (vm-eval '(foreign-procedure "(cs)sampler_stop" () void))
            (vm-eval '(foreign-procedure "(cs)sampler_take" () scheme-object)))))

(define (sampler-available?) (and vm-start #t))

(define (check who)
  (unless vm-start
    (raise (exn:fail:unsupported
            (format "~a: this Racket CS has no built-in stack sampler" who)
            (current-continuation-marks)))))

;; Starts sampling the calling OS thread (Racket threads all run on it,
;; so they are all sampled) at `hz` samples per CPU second. Returns the
;; clock: 'task-clock or 'cycles (a perf event) or 'timer.
(define (sampler-start! #:hz [hz 999])
  (check 'sampler-start!)
  (case (vm-start hz)
    [(0) 'timer] [(1) 'task-clock] [(2) 'cycles]
    [else (raise (exn:fail:unsupported "sampler-start!: sampling is unavailable"
                                       (current-continuation-marks)))]))

(define (sampler-stop!) (check 'sampler-stop!) (vm-stop))

;; The samples since the last take, as a list of (cons stack count), each
;; stack a list of `frame`s, outermost first. Clears them.
(define (sampler-take!)
  (check 'sampler-take!)
  (define bv (vm-take))
  (if (bytes? bv) (parse-folded (bytes->string/utf-8 bv #\?)) '()))

;; ----------------------------------------------------------------------
;; folded stacks: "outer;...;inner count" per line

(define (parse-frame s)
  (define-values (body origin)
    (cond [(string-suffix? s "_[j]") (values (substring s 0 (- (string-length s) 4)) 'program)]
          [(string-suffix? s "_[i]") (values (substring s 0 (- (string-length s) 4)) 'runtime)]
          [(string-prefix? s "[C]") (values (substring s 3) 'c)]
          [else (values s 'unknown)]))
  (cond
    ;; Racket marks a method name with "]!" and escapes a leading bracket with "]"
    [(regexp-match #px"^\\][!^]?(.*)$" body)
     => (lambda (m) (parse-frame (string-append (cadr m) (case origin [(program) "_[j]"] [(runtime) "_[i]"] [else ""]))))]
    ;; Racket names an anonymous procedure by its source: "[...path:line:col"
    [(regexp-match #px"^\\[[!^]?(?:\\.\\.\\.)?(.*?):(\\d+):\\d+$" body)
     => (lambda (m) (frame #f origin (last (string-split (cadr m) "/")) (string->number (caddr m))))]
    [(regexp-match #px"^(.*)@([^@]+?)(?::(\\d+)|#\\d+)?$" body)
     => (lambda (m) (frame (anonymous->false (cadr m)) origin (caddr m)
                           (and (cadddr m) (string->number (cadddr m)))))]
    [else (frame (anonymous->false body) origin #f #f)]))

(define (anonymous->false n) (if (equal? n "[anonymous]") #f n))

(define (parse-folded text)
  (for*/list ([line (in-list (string-split text "\n"))]
              [m (in-value (regexp-match #px"^(.*) (\\d+)$" line))]
              #:when m)
    (cons (map parse-frame (string-split (cadr m) ";")) (string->number (caddr m)))))

(define (read-folded path)
  (parse-folded (call-with-input-file path (lambda (in) (port->string* in)))))
(define (port->string* in)
  (let ([o (open-output-string)]) (copy-port* in o) (get-output-string o)))
(define (copy-port* in out)
  (define b (make-bytes 65536))
  (let loop () (define n (read-bytes-avail! b in))
    (unless (eof-object? n) (write-bytes b out 0 n) (loop))))

(define (frame->string f)
  (define base
    (cond [(frame-name f) => values]
          [(frame-file f) "λ"]
          [else "[anonymous]"]))
  (string-append
   (if (eq? (frame-origin f) 'c) "[C]" "")
   (regexp-replace* #rx"[; ]" base "_")
   (if (frame-file f) (format "@~a~a" (frame-file f) (if (frame-line f) (format ":~a" (frame-line f)) "")) "")
   (case (frame-origin f) [(program) "_[j]"] [(runtime) "_[i]"] [else ""])))

;; Folded stacks that flamegraph.pl (with `--colors java`: program code
;; green, runtime aqua, C yellow), inferno, and speedscope read.
(define (write-folded stacks [out (current-output-port)])
  (for ([s (in-list stacks)])
    (fprintf out "~a ~a\n" (string-join (map frame->string (car s)) ";") (cdr s))))

;; ----------------------------------------------------------------------
;; hiding runtime glue

;; Runtime procedures that only pass control on: they are dropped from the
;; middle of a stack (their own time, when one is the leaf, stays).
(define glue
  (for/hash ([n (in-list '("dynamic-wind" "call-with-values" "apply" "$app" "call/cc" "call/1cc"
                           "call-with-current-continuation" "call-with-composable-continuation"
                           "call-in-empty-metacontinuation-frame"
                           "call-with-empty-metacontinuation-frame-for-swap"
                           "call-in-empty-metacontinuation-frame-for-compose" "call-in-continuation"
                           "call-getting-continuation-attachment" "call-setting-continuation-attachment"
                           "call-consuming-continuation-attachment" "with-continuation-mark*"
                           "call-with-module-prompt" "call-with-continuation-prompt"
                           "call-with-continuation-barrier" "call-with-parameterization"
                           "call-with-immediate-continuation-mark" "invoke" "ksrc" "$as-time-goes-by"))])
    (values n #t)))

;; Simplifies a stack (outermost first) for reading:
;;   * drops the frames before the first program frame (process, module,
;;     and thread start-up), when there is a program frame;
;;   * drops anonymous and pure-control runtime frames that are not the leaf;
;;   * names a remaining anonymous frame after its nearest named caller.
(define (tidy-stack stack)
  (define start (or (index-where stack (lambda (f) (eq? (frame-origin f) 'program))) 0))
  (define fs (drop stack start))
  (define n (length fs))
  (define out
    (for/fold ([out '()] #:result (reverse out))
              ([f (in-list fs)] [i (in-naturals)])
      (define leaf? (= i (sub1 n)))
      (cond
        [(and (eq? (frame-origin f) 'runtime) (not leaf?)
              (or (not (frame-name f)) (hash-ref glue (frame-name f) #f)))
         out]
        [(and (not (frame-name f)) (not (frame-file f)))
         (define caller (for/first ([g (in-list out)] #:when (and (frame-name g) (not (eq? (frame-origin g) 'c))))
                          (frame-name g)))
         ;; with no named caller, typically a module body: say which file it runs
         (define callee (and (not caller)
                             (for/first ([g (in-list (list-tail fs (add1 i)))] #:when (frame-file g))
                               (frame-file g))))
         (cons (struct-copy frame f [name (cond [caller (format "[λ in ~a]" caller)]
                                                [callee (format "[λ → ~a]" callee)]
                                                [else "[λ]"])])
               out)]
        [else (cons f out)])))
  (if (null? out) stack out))

;; tidy-stack over a list of (cons stack count), merging equal results
(define (tidy-stacks stacks)
  (define h (make-hash))
  (define order '())
  (for ([s (in-list stacks)])
    (define k (tidy-stack (car s)))
    (unless (hash-ref h k #f) (set! order (cons k order)))
    (hash-update! h k (lambda (c) (+ c (cdr s))) 0))
  (for/list ([k (in-list (reverse order))]) (cons k (hash-ref h k))))

;; ----------------------------------------------------------------------
;; the profile library's analyzer and renderers

;; Converts samples to the `profile` library's analyzed form, so that its
;; text and graphviz renderers (and `profile->json`) work on them. Each
;; sample stands for 1000/hz milliseconds of CPU time. Runtime frames and C
;; frames are kept unless `tidy?` removed them.
(define (samples->profile stacks #:hz [hz 999])
  (define ms (/ 1000.0 hz))
  ;; the analyzer identifies nodes by `eq?` entries
  (define interned (make-hash))
  (define (entry f)
    (define e (cons (string->symbol (or (frame-name f) (if (frame-file f) "λ" "[anonymous]")))
                    (and (frame-file f) (srcloc (frame-file f) (frame-line f) #f #f #f))))
    (hash-ref! interned e e))
  (define snapshots
    (let loop ([stacks stacks] [t 0.0] [acc '()])
      (cond
        [(null? stacks) acc]
        [else
         (define inner-first (map entry (reverse (car (car stacks)))))
         (define-values (acc* t*)
           (for/fold ([acc acc] [t t]) ([_ (in-range (cdr (car stacks)))])
             (values (cons (list* 0 t inner-first) acc) (+ t ms))))
         (loop (cdr stacks) t* acc*)])))
  (define total (for/sum ([s (in-list stacks)]) (cdr s)))
  ;; the analyzer derives each sample's time from neighboring timestamps,
  ;; and wants the newest first, as the profile library's sampler records them
  (analyze-samples (cons (* total ms) snapshots)))

;; Like `profile-thunk` from the `profile` library, but with the built-in
;; sampler. All Racket threads are sampled (they share the OS thread).
(define (profile-thunk/sampler thunk
                               #:hz [hz 999]
                               #:tidy? [tidy? #t]
                               #:render [render text:render]
                               #:folded [folded-path #f]
                               #:order [order 'topological])
  (sampler-take!) ; discard anything left from earlier
  (sampler-start! #:hz hz)
  (define results
    (dynamic-wind void
                  (lambda () (call-with-values thunk list))
                  sampler-stop!))
  (define raw (map (lambda (s) (cons (below-profiler (car s)) (cdr s))) (sampler-take!)))
  (define stacks (if tidy? (tidy-stacks raw) raw))
  (when folded-path
    (call-with-output-file folded-path #:exists 'truncate
      (lambda (out) (write-folded stacks out))))
  (when render
    (define p (samples->profile stacks #:hz hz))
    (if (procedure-arity-includes? render 2) (render p order) (render p)))
  (apply values results))

;; drops the frames that called the profiler, through its own frame
(define (below-profiler stack)
  (let loop ([fs stack])
    (cond [(null? fs) stack]
          [(equal? (frame-name (car fs)) "profile-thunk/sampler") (if (null? (cdr fs)) stack (cdr fs))]
          [else (loop (cdr fs))])))

(define-syntax-rule (profile/sampler expr kw ...)
  (profile-thunk/sampler (lambda () expr) kw ...))
