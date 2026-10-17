#!/bin/zsh
# Double-click in Finder to keep the service in your own Terminal window.
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print "Нет окружения .venv. Выполните установку из README.md."
else
  .venv/bin/python scripts/start_public.py
fi
print ""
read "?Нажмите Enter, чтобы закрыть это окно. "
