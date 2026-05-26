from __future__ import annotations

import argparse
import os

from flask import Flask, jsonify, render_template

from bbc_springwatch import BBCSpringwatchClient, BBCSpringwatchError


def create_app() -> Flask:
    app = Flask(__name__)
    client = BBCSpringwatchClient()

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            official_live_url=client.official_live_url,
        )

    @app.get("/api/streams")
    def streams():
        try:
            return jsonify(
                {
                    "ok": True,
                    "official_live_url": client.official_live_url,
                    "streams": [stream.to_dict() for stream in client.get_streams()],
                }
            )
        except BBCSpringwatchError as exc:
            return jsonify({"ok": False, "error": str(exc), "streams": []}), 502

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BBC Springwatch wildlife cam dashboard")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=args.debug)
