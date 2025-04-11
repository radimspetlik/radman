import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.table_manager import get_table_manager

radiopharm_bp = Blueprint('radiopharm', __name__, template_folder='templates')

# List of default radiopharmaceutical types.
DEFAULT_TYPES = [
    '18F-FDG',
    '18F-PSMA',
    '18F-FET',
    '18F-Cholin',
    '18F-NaF',
    '18F-FDOPA',
    '18F-Vizamyl (fluemetamol)',
    '68Ga-DOTATOC',
    '68Ga-PSMA-11',
    '68Ga-FAPI',
    '11C-Cholin',
    '11C-Methionin',
    '15O-H2O',
    '13N-NH3'
]

@radiopharm_bp.route('/manage', methods=['GET', 'POST'])
@login_required
def manage():
    table_manager = get_table_manager()
    if request.method == 'POST':
        # Process submitted form data.
        # The inputs use a nested naming convention: records[<index>][field]
        form_data = request.form.to_dict(flat=False)
        records = []
        index = 0
        while True:
            key_prefix = f"records[{index}]"
            type_key = f"{key_prefix}[type]"
            if type_key in form_data:
                record = {}
                record['type'] = form_data.get(type_key)[0]
                # The checkbox for availability is sent only if checked.
                record['available'] = (f"{key_prefix}[available]" in form_data)
                record['price'] = form_data.get(f"{key_prefix}[price]", [""])[0]
                record['time_slot'] = form_data.get(f"{key_prefix}[time_slot]", ["anytime"])[0]
                # Retrieve row_key if it exists.
                record['row_key'] = form_data.get(f"{key_prefix}[row_key]", [""])[0]
                records.append(record)
                index += 1
            else:
                break

        # Build a batch of entities to upsert in the "pharmaceutical" table.
        batch = []
        for i, rec in enumerate(records):
            # Use an existing row key if provided, otherwise generate one.
            row_key = rec['row_key'] if rec['row_key'] else f"{rec['type']}_{i}"
            entity = {
                'PartitionKey': current_user.username,  # Tie this record to the current user.
                'RowKey': row_key,
                'type': rec['type'],
                'available': str(rec['available']),
                'price': rec['price'],
                'time_slot': rec['time_slot']
            }
            batch.append(entity)

        try:
            table_manager.upload_batch_to_table("pharmaceutical", batch)
            flash("Radiopharmaceutical data saved successfully.")
        except Exception as e:
            current_app.logger.error("Failed to save pharmaceutical data: %s", e)
            flash("Failed to save data. Please check the logs.")
        return redirect(url_for('radiopharm.manage'))
    else:
        # GET request: load the records for the current user.
        query_str = "PartitionKey eq '{}'".format(current_user.username)
        records = list(table_manager.query_entities("pharmaceutical", query=query_str))
        # If no records exist for the user, create default entries (one per type).
        if not records:
            records = []
            for t in DEFAULT_TYPES:
                records.append({
                    'type': t,
                    'available': False,
                    'price': "",
                    'time_slot': "anytime"
                    # Note: no row_key because these entries haven't been saved yet.
                })
        else:
            # Convert the stored 'available' string to a boolean.
            for rec in records:
                rec['available'] = rec.get('available', 'False') == 'True'
        return render_template('manage.html', records=records)
