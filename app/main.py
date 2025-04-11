import os
from flask import render_template, redirect, url_for

from app.app_init import get_login_manager, get_app

app = get_app(__name__)

login_manager = get_login_manager()


# We assume these environment variables are set:
#   STORAGE_ACCOUNT_NAME, STORAGE_ACCOUNT_KEY
#   BLOB_CONNECTION_STRING, BLOB_CONTAINER_NAME
# Or you adapt them accordingly for your environment.


@app.route("/")
def root():
    return redirect(url_for("user.login"))


# ----------------------------------------------------------------
#  Run
# ----------------------------------------------------------------
if __name__ == "__main__":
    # Typical run. In production, you'd use gunicorn or similar.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
