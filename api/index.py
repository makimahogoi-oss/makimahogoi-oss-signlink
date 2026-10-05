"""Vercel entrypoint.

Vercel's Python runtime loads a file named app.py / index.py / server.py /
main.py / wsgi.py (also inside src/, app/ or api/) and expects a top-level
WSGI variable called ``app``. This file is that entrypoint; everything else
lives in the gramly package.

Only the website runs on Vercel. The Telegram bot (gramly/botapp) needs a long
lived process and must run on a normal host, not in a serverless function.
"""

from gramly.webapp.server import create_app

app = create_app()

# WSGI servers expect callable: the object above already is one.
application = app