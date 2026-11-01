;; usage: scheme -q --script compile1.ss SODIR OUTDIR SRCDIR FILE ...
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
  (for-each
    (lambda (f)
      (compile-file (string-append src "/" f) (string-append out "/" f ".so")))
    (cdddr args)))
