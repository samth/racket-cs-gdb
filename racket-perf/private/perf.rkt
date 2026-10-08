#lang racket/base
(require json racket/file racket/list racket/path racket/runtime-path
         racket/string racket/system "perf-map.rkt" "perf-linux.rkt")
(provide main)
(define-runtime-path exec-helper "perf-exec.rkt")
(define (now) (/ (current-inexact-monotonic-milliseconds) 1000.0))
(define (this-racket)
  (path->string (simplify-path (find-executable-path (find-system-path 'exec-file)) #t)))

(define (parse-options argv)
  (define options (make-hash '((frequency . 999) (event . "cpu-clock:u") (rows . 20)
                              (unique . #f) (interactive . #f) (duration . #f)
                              (equates . #f) (output . #f) (racket . #f))))
  (define (help)
    (display "usage: raco racket-perf [options] script [arguments ...]\n\nRun Racket CS, record a flat perf profile, and print named hot procedures.\n\n  --racket PATH       Racket executable (default: Racket running this command)\n  -o, --output DIR    New result directory (default: timestamped directory)\n  -F, --frequency N   Samples/second (default: 999)\n  -e, --event EVENT   Perf event (default: cpu-clock:u)\n  --duration SECONDS  Stop the program after this many seconds\n  --equates PATH      Matching Chez ta6le equates.h\n  --unique            Append code-object addresses to Scheme names\n  --interactive       Open perf's interactive report after saving the text report\n  --rows N            Hot procedures printed (default: 20; full report saved)\n  -h, --help          Show this help\n\nUse -- before Racket flags. For a toolchain: rackup run ID -- raco racket-perf ...\n")
    (values #f '()))
  (let loop ([args argv])
    (cond [(and (pair? args) (regexp-match #px"^(--[^=]+)=(.*)$" (car args)))
           => (lambda (m) (loop (append (cdr m) (cdr args))))]
          [(and (pair? args) (regexp-match #px"^(-[oFe])(.+)$" (car args)))
           => (lambda (m) (loop (append (cdr m) (cdr args))))]
          [(null? args) (values options '())]
          [(equal? (car args) "--") (values options (cdr args))]
          [(member (car args) '("--help" "-h")) (help)]
          [(member (car args) '("--unique" "--interactive"))
           (hash-set! options (if (equal? (car args) "--unique") 'unique 'interactive) #t)
           (loop (cdr args))]
          [(member (car args) '("--racket" "--output" "-o" "--frequency" "-F"
                                "--event" "-e" "--duration" "--equates" "--rows"))
           (when (null? (cdr args)) (error 'racket-perf "~a needs a value" (car args)))
           (define key (cond [(member (car args) '("--output" "-o")) 'output]
                             [(member (car args) '("--frequency" "-F")) 'frequency]
                             [(member (car args) '("--event" "-e")) 'event]
                             [else (string->symbol (substring (car args) 2))]))
           (define value (cadr args))
           (hash-set! options key
                      (if (memq key '(frequency rows duration))
                          (let ([n (string->number value)])
                            (unless (and (real? n) (> n 0) (< n +inf.0)
                                         (or (eq? key 'duration) (exact-integer? n)))
                              (error 'racket-perf "~a must be positive~a" key
                                     (if (eq? key 'duration) " and finite" " and an integer")))
                            n) value))
           (loop (cddr args))]
          [(string-prefix? (car args) "-") (error 'racket-perf "unknown option ~a; use -- before Racket flags" (car args))]
          [else (values options args)])))

(define (control perf writer reader command)
  (control-write writer command)
  (define deadline (+ (now) 10))
  (let loop ()
    (define ack (control-read reader))
    (cond [(and ack (member #"ack" (regexp-split #rx#"[\n\r\0]+" ack))) (void)]
          [(or (> (now) deadline) (not (eq? (subprocess-status perf) 'running)))
           (error 'perf "did not acknowledge ~a; see perf.log" command)]
          [else (sleep .01) (loop)])))
(define (stopped? status) (= (bitwise-and status #xff) #x7f))
(define (stop-signal status) (bitwise-and (arithmetic-shift status -8) #xff))
(define (exit-code status)
  (if (zero? (bitwise-and status #x7f))
      (bitwise-and (arithmetic-shift status -8) #xff)
      (- (bitwise-and status #x7f))))

(define (record command options output)
  (define (opt key) (hash-ref options key))
  (define metadata
    (make-hash (list (cons 'command command) (cons 'event (opt 'event))
                    (cons 'frequency (opt 'frequency)) (cons 'unique (opt 'unique))
                    (cons 'duration_limit (or (opt 'duration) 'null))
                    (cons 'toolchain (or (getenv "RACKUP_TOOLCHAIN") 'null)))))
  (define pid #f)
  (define perf #f)
  (define log #f)
  (define fds '())
  (define fifo-paths '())
  (define control-directory #f)
  (define sample-end #f)
  (define map-error 'null)
  (define start #f)
  (define duration-reached #f)
  (define termination #f)
  (define interrupted #f)
  (define writer #f)
  (define reader #f)
  (define (snapshot)
    (unless sample-end
      (set! sample-end (now))
      (control perf writer reader "disable")
      (with-handlers ([exn:fail? (lambda (e) (set! map-error (exn-message e)))])
        (for ([(key value) (in-hash (capture-map pid output (opt 'equates) (opt 'unique)))])
          (hash-set! metadata key value)))))
  (define (finish-perf timeout)
    (unless (sync/timeout timeout perf)
      (subprocess-kill perf #t)
      (subprocess-wait perf)))
  (dynamic-wind
   void
   (lambda ()
     (with-handlers ([exn? (lambda (e) (hash-set! metadata 'error (exn-message e)) (raise e))])
       (set! pid (spawn-executable (append (list (this-racket) (path->string exec-helper)) command)))
       (hash-set! metadata 'pid pid)
       ;; Poll rather than blocking the Racket scheduler inside waitpid.
       (define initial
         (let loop () (or (wait-child pid #t) (begin (sleep .01) (loop)))))
       (unless (and (stopped? initial) (= (stop-signal initial) 5))
         (set! pid #f)
         (error 'ptrace "Racket could not start under ptrace"))
       (ptrace #x4200 pid (bitwise-ior #x10 #x40 #x100000))
       ;; Perf separates FIFO paths with a comma, so keep them outside the
       ;; user-selected output path (which may itself contain a comma).
       (set! control-directory (make-temporary-file "racket-perf-control-~a" 'directory "/tmp"))
       (define control-path (build-path control-directory "control"))
       (define ack-path (build-path control-directory "ack"))
       (for ([path (in-list (list control-path ack-path))])
         (set! fds (cons (make-control path) fds))
         (set! fifo-paths (cons path fifo-paths)))
       (set! writer (cadr fds))
       (set! reader (car fds))
       (set! log (open-output-file (build-path output "perf.log")))
       (define-values (proc stdout stdin stderr)
         (parameterize ([subprocess-group-enabled #t])
           (subprocess log (current-input-port) log (find-executable-path "perf")
                       "record" "-e" (opt 'event) "-F" (number->string (opt 'frequency))
                       "-o" (path->string (build-path output "perf.data")) "-p" (number->string pid)
                       "--delay=-1" (format "--control=fifo:~a,~a" control-path ack-path))))
       (set! perf proc)
       (control perf writer reader "enable")
       (set! start (now))
       (ptrace 7 pid)
       (define status
         (let loop ()
           (define status (wait-child pid #t))
           (define t (now))
           (cond
             [(and status (not (stopped? status))) status]
             [status
              (define event (arithmetic-shift status -16))
              (when (or (= event 6) termination) (snapshot))
              (ptrace 7 pid (if (zero? event) (stop-signal status) 0))
              (loop)]
             [else
              (unless (eq? (subprocess-status perf) 'running)
                (error 'perf "stopped unexpectedly; see perf.log"))
              (cond [(and (not termination)
                          (or interrupted (and (opt 'duration) (>= (- t start) (opt 'duration)))))
                     (set! duration-reached (not interrupted))
                     (kill-child pid (or interrupted 15))
                     (set! termination t)]
                    [(and termination (> (- t termination) 3))
                     (kill-child pid 9) (set! termination t)])
              ;; Defer breaks during ptrace/map/report operations. Deliver them
              ;; here and forward them to the traced process before cleanup.
              (with-handlers ([exn:break? (lambda (e)
                                           (set! interrupted (if (exn:break:terminate? e) 15 2)))])
                (parameterize-break #t (sleep .01)))
              (loop)])))
       (set! pid #f)
       (hash-set! metadata 'elapsed_seconds (- (now) start))
       (hash-set! metadata 'recording_seconds (- (or sample-end (now)) start))
       (hash-set! metadata 'exit_code (exit-code status))
       (hash-set! metadata 'duration_reached duration-reached)
       (hash-set! metadata 'interrupted_signal (or interrupted 'null))
       (hash-set! metadata 'map_error map-error)
       (when (eq? (subprocess-status perf) 'running) (control-write writer "stop"))
       (finish-perf 10)
       (unless (zero? (subprocess-status perf)) (error 'perf "recording failed; see perf.log"))
       metadata))
   (lambda ()
     (when pid
       (kill-child pid 9)
       (with-handlers ([exn:fail? void])
         (let loop ()
           (define status (wait-child pid #t))
           (cond [(not status) (sleep .01) (loop)]
                 [(stopped? status) (ptrace 7 pid) (loop)]))))
     (when (and perf (eq? (subprocess-status perf) 'running))
       (subprocess-kill perf #f) (finish-perf 5))
     (when log (close-output-port log))
     (for-each close-control fds)
     (for ([path (in-list fifo-paths)]) (when (file-exists? path) (delete-file path)))
     (when control-directory (delete-directory control-directory))
     (call-with-output-file (build-path output "run.json") #:exists 'truncate/replace
       (lambda (out) (write-json metadata out) (newline out))))))

(define (standalone-environment)
  (define env (environment-variables-copy (current-environment-variables)))
  (when (and (find-executable-path "rackup") (not (getenv "RACKUP_TOOLCHAIN")))
    (define (query . args)
      (define buffer (open-output-string))
      (unless (parameterize ([current-output-port buffer])
                (apply system* (find-executable-path "rackup") args))
        (error 'rackup "cannot configure the profiling environment"))
      (get-output-string buffer))
    (define ids (string-split (query "list" "--ids")))
    (define selected (car (string-split (string-trim (query "current" "line")) "\t")))
    (unless (member selected ids) (error 'rackup "no installed toolchain selected"))
    ;; Supported rackup subprocess configuration, retained only in memory.
    (define bindings
      (read (open-input-string
             (query "run" selected "--" "racket" "-e"
                    "(let ([e (current-environment-variables)]) (write (for/list ([k (in-list (environment-variables-names e))]) (cons k (environment-variables-ref e k)))))"))))
    (for ([binding (in-list bindings)])
      (environment-variables-set! env (car binding) (cdr binding))))
  env)

(define (main [argv (vector->list (current-command-line-arguments))] #:standalone? [standalone? #f])
  (define output #f)
  (with-handlers ([exn:fail? (lambda (e)
                             (eprintf "racket-perf: ~a~a\n" (exn-message e)
                                      (if output (format " (results: ~a)" output) ""))
                             (when (and output (file-exists? (build-path output "perf.log")))
                               (eprintf "~a" (file->string (build-path output "perf.log"))))
                             1)])
    (define-values (options arguments) (parse-options argv))
    (cond
      [(not options) 0]
      [else
       (unless (eq? (system-type 'os) 'unix) (error 'racket-perf "requires Linux"))
       (unless (find-executable-path "perf") (error 'racket-perf "perf is not installed"))
       (define executable (if (hash-ref options 'racket)
                              (find-executable-path (hash-ref options 'racket))
                              (string->path (this-racket))))
       (unless executable (error 'racket-perf "cannot find Racket; use --racket PATH"))
       (when (hash-ref options 'equates) (read-equates (hash-ref options 'equates)))
       (set! output
             (path->complete-path
              (or (hash-ref options 'output)
                  (format "racket-perf-~a-~a" (current-seconds)
                          (inexact->exact (floor (current-inexact-milliseconds)))))))
       (when (or (file-exists? output) (directory-exists? output))
         (error 'racket-perf "output already exists: ~a" output))
       (make-directory* output)
       (eprintf "Recording to ~a\n" output)
       (parameterize-break #f
         (define metadata
           (parameterize ([current-environment-variables
                           (if (and standalone? (not (hash-ref options 'racket)))
                               (standalone-environment) (current-environment-variables))])
             (record (cons (path->string (simplify-path executable #t)) arguments) options output)))
         (define report-path (build-path output "report.txt"))
         (call-with-output-file report-path
           (lambda (out)
             (unless (parameterize ([current-output-port out])
                       (system* (find-executable-path "perf") "report" "-i"
                                (path->string (build-path output "perf.data")) "--stdio"
                                "--stdio-color=never" "--no-children" "--sort=dso,symbol" "-n" "--percent-limit=0"))
               (error 'perf "report failed"))))
         (let loop ([lines (file->lines report-path)] [rows 0])
           (unless (null? lines)
             (define next (+ rows (if (regexp-match? #px"^\\s*\\d.*%" (car lines)) 1 0)))
             (if (> next (hash-ref options 'rows))
                 (displayln "... full profile in report.txt")
                 (begin (displayln (car lines)) (loop (cdr lines) next)))))
         (eprintf "Saved recording, report, map, and metadata in ~a\n" output)
         (unless (eq? (hash-ref metadata 'map_error) 'null)
           (error 'racket-perf "could not capture Scheme symbols: ~a" (hash-ref metadata 'map_error)))
         (when (hash-ref options 'interactive)
           (parameterize-break #t
             (unless (system* (find-executable-path "perf") "report" "-i"
                              (path->string (build-path output "perf.data")))
               (error 'perf "interactive report failed"))))
         (define code (hash-ref metadata 'exit_code))
         (cond [(hash-ref metadata 'duration_reached) 0]
               [(integer? (hash-ref metadata 'interrupted_signal))
                (+ 128 (hash-ref metadata 'interrupted_signal))]
               [else (if (< code 0) (- 128 code) code)]))])))
