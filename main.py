import sys

if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        from editor.selftest import run
        raise SystemExit(run(sys.argv[2]))
    from editor.gui import main
    raise SystemExit(main())
