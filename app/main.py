import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash
from azure.data.tables import TableEntity

from app.table_manager import get_table_manager
from app.blob_manager import get_blob_manager

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")  # for flashing messages, sessions, etc.

# We assume these environment variables are set:
#   STORAGE_ACCOUNT_NAME, STORAGE_ACCOUNT_KEY
#   BLOB_CONNECTION_STRING, BLOB_CONTAINER_NAME
# Or you adapt them accordingly for your environment.


@app.route("/")
def root():
    return render_template("index.html")


# ----------------------------------------------------------------
#  Run
# ----------------------------------------------------------------
if __name__ == "__main__":
    # Typical run. In production, you'd use gunicorn or similar.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
