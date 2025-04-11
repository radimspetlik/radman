import logging
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.table_manager import get_table_manager

radiopharm_bp = Blueprint('radiopharm', __name__, template_folder='templates')

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
        form_data = request.form.to_dict(flat=False)
        records = []
        index = 0
        # Gather all rows from the form
        while True:
            key_prefix = f"records[{index}]"
            type_key = f"{key_prefix}[type]"
            if type_key in form_data:
                record = {}
                record['type'] = form_data.get(type_key)[0]
                record['available'] = (f"{key_prefix}[available]" in form_data)
                record['price'] = form_data.get(f"{key_prefix}[price]", [""])[0]
                record['time_slot'] = form_data.get(f"{key_prefix}[time_slot]", ["anytime"])[0]
                record['row_key'] = form_data.get(f"{key_prefix}[row_key]", [""])[0]
                records.append(record)
                index += 1
            else:
                break

        # Fetch existing records to figure out what's been removed
        query_str = "PartitionKey eq '{}'".format(current_user.username)
        existing_records = list(table_manager.query_entities("pharmaceutical", query=query_str))
        existing_keys = set(r['RowKey'] for r in existing_records)

        # Build a batch for upsert
        batch = []
        new_row_keys = set()
        for i, rec in enumerate(records):
            if rec['row_key']:
                row_key = rec['row_key']
            else:
                # Generate a new row key if none exists
                row_key = str(uuid.uuid4())

            entity = {
                'PartitionKey': current_user.username,
                'RowKey': row_key,
                'type': rec['type'],
                'available': str(rec['available']),
                'price': rec['price'],
                'time_slot': rec['time_slot']
            }
            new_row_keys.add(row_key)
            batch.append(entity)

        # Remove any records from the DB that are no longer present in the form
        to_delete = existing_keys - new_row_keys
        try:
            for key in to_delete:
                table_manager.delete_entities("pharmaceutical", [{'PartitionKey': current_user.username, 'RowKey': key}])

            # Upsert the current batch
            table_manager.upload_batch_to_table("pharmaceutical", batch)
            flash("Radiopharmaceutical data saved successfully.")
        except Exception as e:
            current_app.logger.error("Failed to save pharmaceutical data: %s", e)
            flash("Failed to save data. Please check the logs.")

        return redirect(url_for('radiopharm.manage'))

    else:
        # GET request: load existing records
        query_str = "PartitionKey eq '{}'".format(current_user.username)
        records = list(table_manager.query_entities("pharmaceutical", query=query_str))
        if not records:
            # If none exist, populate defaults
            records = []
            for t in DEFAULT_TYPES:
                records.append({
                    'type': t,
                    'available': False,
                    'price': "",
                    'time_slot': "anytime"
                })
        else:
            # Convert 'available' to bool
            for rec in records:
                rec['available'] = (rec.get('available', 'False') == 'True')
        return render_template('manage.html', records=records)
