;; usage: scheme -q --script compile-timed.ss SODIR OUTDIR SRCDIR FILE ...
;; prints "elapsed-ms N cpu-ms M" for the compile-file calls to stderr
;; SODIR is the build's own ta6le/s (cmacros.so etc.); sources come from a fixed copy of s/
(let* ([args (command-line-arguments)]
       [sodir (car args)]
       [out (cadr args)]
       [src (caddr args)])
  (source-directories (list src))
  (library-directories (list (cons "/tmp/mtrace/bench/nanopass" sodir)))
  (optimize-level 3)
  (debug-level 0)
  (generate-inspector-information #f)
  (for-each (lambda (f) (load (string-append sodir "/" f)))
    '("cmacros.so" "priminfo.so" "primvars.so" "env.so" "setup.so"))
  (subset-mode 'system)
  (let ([r0 (real-time)] [c0 (cpu-time)])
    (parameterize ([current-output-port (open-output-string)])
      (for-each
        (lambda (f)
          (compile-file (string-append src "/" f) (string-append out "/" f ".so")))
        (cdddr args)))
    (fprintf (current-error-port) "elapsed-ms ~a cpu-ms ~a\n" (- (real-time) r0) (- (cpu-time) c0))))
