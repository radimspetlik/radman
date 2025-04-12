from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import json
from app.constants import PHARM_TABLE, DAYSETUP_TABLE
from app.table_manager import get_table_manager

daysetup_bp = Blueprint('daysetup', __name__, template_folder='templates')


@login_required
@daysetup_bp.route('/daysetup', methods=['GET', 'POST'])
def plan_daysetup():
    user_id = current_user.username
    table_manager = get_table_manager()

    if request.method == 'POST':
        # Retrieve and validate the generator activity input.
        try:
            generator_activity = float(request.form.get('generator_activity', 1.85))
        except (ValueError, TypeError):
            flash("Generator Activity must be a number.", "error")
            return redirect(url_for('daysetup.plan_daysetup'))

        # Build the record entity to store, without QAActivity or pharmaceutical selections.
        entity = {
            'PartitionKey': user_id,
            'RowKey': 'default',  # using a constant RowKey to store a single day setup per user
            'GeneratorActivity': generator_activity
        }

        # Save (upsert) the day setup record.
        table_manager.upload_batch_to_table(DAYSETUP_TABLE, [entity])
        flash("Day setup saved successfully.", "success")
        return redirect(url_for('daysetup.plan_daysetup'))

    # For GET requests, try to load an existing day setup record.
    daysetup_record = table_manager.get_entity(DAYSETUP_TABLE, user_id, "default")
    if daysetup_record:
        try:
            generator_activity = float(daysetup_record.get('GeneratorActivity', 1.85))
        except (ValueError, TypeError):
            generator_activity = 1.85
    else:
        generator_activity = 1.85

    return render_template("daysetup.html", generator_activity=generator_activity)
