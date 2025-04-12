import logging
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.constants import PHARM_TABLE
from app.table_manager import get_table_manager

radiopharm_bp = Blueprint('radiopharm', __name__, template_folder='templates')

# Default set of pharmaceuticals that each user starts with if none exist
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
    partition_key = current_user.username

    if request.method == 'POST':
        # Convert the raw form data into a list of (pharmaceutical) records
        form_data = request.form.to_dict(flat=False)
        records = []
        index = 0

        while True:
            base_name = f"records[{index}]"
            type_key = f"{base_name}[type]"
            if type_key in form_data:
                pharm_type = form_data[type_key][0]

                # Convert "available" checkbox to boolean
                available = (f"{base_name}[available]" in form_data)

                # Price is straightforward
                price = form_data.get(f"{base_name}[price]", [""])[0]

                # Here we store multiple time slots as a list
                # The name in the HTML is records[{i}][time_slots][]
                time_slots = form_data.get(f"{base_name}[time_slots][]", [])

                # Some DB columns
                entity = {
                    'PartitionKey': partition_key,
                    'RowKey': pharm_type,  # Pharmaceutical name = unique RowKey
                    'type': pharm_type,
                    'available': str(available),
                    'price': price,
                    # Store time_slots as JSON in the DB
                    'time_slots': json.dumps(time_slots)
                }
                records.append(entity)
                index += 1
            else:
                break

        # Now, we want to upsert these and remove anything else from the DB
        # that the user no longer has in their form. Because each pharma name
        # must be unique per user, let's do the following:
        existing = list(table_manager.query_entities(
            PHARM_TABLE,
            query="PartitionKey eq '{}'".format(partition_key)
        ))
        existing_keys = {item['RowKey'] for item in existing}
        new_keys = {r['RowKey'] for r in records}

        # Delete old pharmaceuticals not in the new form
        to_delete = existing_keys - new_keys

        try:
            for key in to_delete:
                table_manager.delete_entities(PHARM_TABLE, [{'PartitionKey': partition_key, 'RowKey': key}])

            # Upsert the new set
            table_manager.upload_batch_to_table(PHARM_TABLE, records)
            flash("Radiopharmaceutical data saved successfully.")
        except Exception as e:
            current_app.logger.error("Failed to save pharmaceutical data: %s", e)
            flash("Failed to save data. Please check the logs.")

        return redirect(url_for('radiopharm.manage'))

    else:
        # GET request: load records
        existing_records = list(table_manager.query_entities(
            PHARM_TABLE,
            query="PartitionKey eq '{}'".format(partition_key)
        ))

        if not existing_records:
            # If there are no records at all, populate defaults
            # with empty JSON array for time_slots
            records = []
            for pharm_type in sorted(DEFAULT_TYPES):
                records.append({
                    'PartitionKey': partition_key,
                    'RowKey': pharm_type,
                    'type': pharm_type,
                    'available': False,
                    'price': "",
                    'time_slots': json.dumps(["anytime"])  # store as "[]"
                })
        else:
            # Convert 'available' to bool, parse the JSON time_slots
            records = []
            for rec in existing_records:
                rec['available'] = (rec.get('available', 'False') == 'True')
                if 'time_slots' in rec:
                    try:
                        rec['time_slots'] = json.loads(rec['time_slots'])
                    except:
                        rec['time_slots'] = []
                else:
                    rec['time_slots'] = []
                records.append(rec)

        return render_template('manage.html', records=records)
