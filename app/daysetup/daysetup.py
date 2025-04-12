from flask import Blueprint


import json
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.constants import PHARM_TABLE, DAYSETUP_TABLE
from app.table_manager import get_table_manager

daysetup_bp = Blueprint('daysetup', __name__, template_folder='templates')


@login_required
@daysetup_bp.route('/daysetup', methods=['GET', 'POST'])
def plan_daysetup():
    user_id = current_user.username
    table_manager = get_table_manager()

    # Query the available radiopharmaceuticals for the current user.
    pharm_query = f"PartitionKey eq '{user_id}'"
    pharm_records = list(table_manager.query_entities(PHARM_TABLE, pharm_query))

    if request.method == 'POST':
        # Retrieve and validate the form inputs.
        try:
            generator_activity = float(request.form.get('generator_activity', 1.85))
        except (ValueError, TypeError):
            flash("Generator Activity must be a number.", "error")
            return redirect(url_for('daysetup.plan_daysetup'))

        try:
            qa_activity = float(request.form.get('qa_activity', 0))
        except (ValueError, TypeError):
            flash("QA Activity must be a number.", "error")
            return redirect(url_for('daysetup.plan_daysetup'))

        # Retrieve radiopharmaceutical selections (may be multiple)
        selected_pharms = request.form.getlist('pharmaceuticals')

        # Build the record entity to store.
        entity = {
            'PartitionKey': user_id,
            'RowKey': 'default',  # using a constant RowKey to store a single day setup per user
            'GeneratorActivity': generator_activity,
            'QAActivity': qa_activity,
            'SelectedPharmaceuticals': json.dumps(selected_pharms)
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
        try:
            qa_activity = float(daysetup_record.get('QAActivity', 0))
        except (ValueError, TypeError):
            qa_activity = 0
        selected_pharms = json.loads(daysetup_record.get('SelectedPharmaceuticals', '[]'))
    else:
        generator_activity = 1.85
        qa_activity = 0
        selected_pharms = []

    # skip records that are not available
    pharm_records = list(filter(lambda x: x['available'] == 'True', pharm_records))

    return render_template("daysetup.html",
                           generator_activity=generator_activity,
                           qa_activity=qa_activity,
                           selected_pharms=selected_pharms,
                           pharm_records=pharm_records)
