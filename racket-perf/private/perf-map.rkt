#lang racket/base
(require racket/file racket/list racket/path racket/string)
(provide default-layout read-equates equates-for-executable scheme-string
         find-code-objects write-perf-map capture-map)

(define default-layout
  (hash 'type_code #xbe 'code_type_disp 1 'code_length_disp 9
        'code_name_disp #x19 'code_data_disp #x41 'code_flags_offset 8
        'type_typed_object 7 'type_string 2 'string_type_disp 1
        'string_data_disp 9 'string_length_offset 4 'string_char_bytes 4
        'char_data_offset 8 'type_char #x16 'sfalse 6))

(define (read-equates path)
  (define values (make-hash))
  (for ([line (in-list (file->lines path))])
    (define m (regexp-match #px"^#define\\s+(\\w+)\\s+(?:\\(ptr\\))?(-?0x[0-9a-fA-F]+|-?\\d+)\\s*$" line))
    (when m
      (define key (string->symbol (cadr m)))
      (when (hash-has-key? default-layout key)
        (define str (caddr m))
        (hash-set! values key (if (regexp-match? #rx"0x" str)
                                 (* (if (string-prefix? str "-") -1 1)
                                    (string->number (regexp-replace #rx"^-?0x" str "") 16))
                                 (string->number str))))))
  (define missing (filter (lambda (key) (not (hash-has-key? values key)))
                          (hash-keys default-layout)))
  (unless (null? missing) (error 'read-equates "~a lacks ~a" path missing))
  values)

(define (equates-for-executable exe)
  (define dir (path-only (simplify-path (resolve-path (path->complete-path exe)) #t)))
  (for/or ([root (in-list (list dir (build-path dir 'up "src" "build" "cs" "c")))])
    (define path (build-path root "ChezScheme" "boot" "ta6le" "equates.h"))
    (and (file-exists? path) path)))

(define (uint bytes [start 0] [size 8])
  (integer-bytes->integer bytes #f #f start (+ start size)))
(define (scheme-string read s L)
  (define (v key) (hash-ref L key))
  (cond
    [(= s (v 'sfalse)) "#f"]
    [(not (= (bitwise-and s 7) (v 'type_typed_object))) #f]
    [else
     (define header (read (+ s (v 'string_type_disp)) 8))
     (and header
          (= (bitwise-and (uint header) 7) (v 'type_string))
          (let* ([n (arithmetic-shift (uint header) (- (v 'string_length_offset)))]
                 [data (and (<= n 1000) (read (+ s (v 'string_data_disp)) (* n (v 'string_char_bytes))))])
            (and data
                 (let ([chars
                        (for/list ([i (in-range n)])
                          (define c (uint data (* i 4) 4))
                          (define cp (arithmetic-shift c (- (v 'char_data_offset))))
                          (and (= (bitwise-and c #xff) (v 'type_char))
                               (<= cp #x10ffff) (not (<= #xd800 cp #xdfff))
                               (integer->char cp)))])
                   (and (andmap values chars) (list->string chars))))))]))

(define (find-code-objects read mappings L [chunk (arithmetic-shift 16 20)])
  (define (v key) (hash-ref L key))
  (define entries '())
  (for ([mapping (in-list mappings)])
    (define hi (cdr mapping))
    (let chunks ([a (car mapping)])
      (when (< a hi)
        (define end (min (+ a chunk) hi))
        (define data (read a (- end a)))
        (define next end)
        (when data
          (let scan ([i 0])
            (when (< i (bytes-length data))
              (cond
                [(and (= (bytes-ref data i) (v 'type_code))
                      (zero? (modulo (+ a i) 8)) (<= (+ i 16) (bytes-length data)))
                 (define tw (uint data i))
                 (define n (uint data (+ i 8)))
                 (define p (- (+ a i) (v 'code_type_disp)))
                 (define start (+ p (v 'code_data_disp)))
                 (define name-ptr
                   (and (< (arithmetic-shift tw (- (v 'code_flags_offset))) (expt 2 16))
                        (< 0 n (expt 2 24)) (<= (+ start n) hi)
                        (read (+ p (v 'code_name_disp)) 8)))
                 (define name (and name-ptr (scheme-string read (uint name-ptr) L)))
                 (cond [name
                        (set! entries (cons (list start n name) entries))
                        (if (>= (+ start n) end)
                            (set! next (+ start n))
                            (scan (- (+ start n) a)))]
                       [else (scan (add1 i))])]
                [else (scan (add1 i))]))))
        (chunks next))))
  (reverse entries))

(define (write-perf-map path entries [unique? #f])
  (call-with-output-file path #:exists 'truncate/replace
    (lambda (out)
      (for ([entry (in-list (sort entries < #:key car))])
        (define name (caddr entry))
        (define printable
          (cond [unique? (format "~a [0x~x]" name (car entry))]
                [(<= (string-length name) 2) (format "[~a]" name)]
                [else name]))
        (fprintf out "~x ~x ~a\n" (car entry) (cadr entry)
                 (string-replace printable "\n" " "))))))

(define (capture-map pid output equates unique?)
  (define layout-path (or equates (equates-for-executable (format "/proc/~a/exe" pid))))
  (define L (if layout-path (read-equates layout-path) default-layout))
  (define mappings
    (for/list ([line (in-list (file->lines (format "/proc/~a/maps" pid)))]
               #:do [(define fields (string-split line))]
               #:when (and (string-contains? (cadr fields) "r")
                           (or (= (length fields) 5) (equal? (list-ref fields 5) "[heap]"))))
      (define addresses (string-split (car fields) "-"))
      (cons (string->number (car addresses) 16) (string->number (cadr addresses) 16))))
  (define entries
    (call-with-input-file (format "/proc/~a/mem" pid)
      (lambda (mem)
        (file-stream-buffer-mode mem 'none)
        (find-code-objects
         (lambda (address n)
           (with-handlers ([exn:fail? (lambda (_) #f)])
             (file-position mem address)
             (define data (read-bytes n mem))
             (and (bytes? data) (= (bytes-length data) n) data))) mappings L))))
  (define filename (format "perf-~a.map" pid))
  (define path (build-path output filename))
  (write-perf-map path entries unique?)
  (define tmp (make-temporary-file "chez-perf-~a" #f "/tmp"))
  (dynamic-wind void
                (lambda () (copy-file path tmp #t)
                  (rename-file-or-directory tmp (build-path "/tmp" filename) #t))
                (lambda () (when (file-exists? tmp) (delete-file tmp))))
  (hash 'map filename 'code_objects (length entries)
        'layout (if layout-path (path->string layout-path) "built-in values for Racket CS 9.3.0.8")))
