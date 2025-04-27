import logging
import json
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.constants import PHARM_TABLE
from app.table_manager import get_table_manager

radiopharm_bp = Blueprint('radiopharm', __name__, template_folder='templates')

# Default set of pharmaceuticals that each user starts with if none exist.
DEFAULT_TYPES = [
    ('18F-FDG', 109.8),
    ('18F-PSMA', 109.8),
    ('18F-FET', 109.8),
    ('18F-Cholin', 109.8),
    ('18F-NaF', 109.8),
    ('18F-FDOPA', 109.8),
    ('18F-Vizamyl (fluemetamol)', 109.8),
    ('68Ga-DOTATOC', 67.7),
    ('68Ga-PSMA-11', 67.7),
    ('68Ga-FAPI', 67.7),
    ('11C-Cholin', 20.4),
    ('11C-Methionin', 20.4),
    ('15O-H2O', 2.03),
    ('13N-NH3', 9.96),
]

@radiopharm_bp.route('/manage', methods=['GET'])
@login_required
def manage():
    """List all radiopharmaceuticals for the current user."""
    table_manager = get_table_manager()
    partition_key = current_user.username

    # Query user records.
    existing_records = list(table_manager.query_entities(
        PHARM_TABLE,
        query="PartitionKey eq '{}'".format(partition_key)
    ))

    # If no record exists, populate defaults.
    if not existing_records:
        records = []
        for pharm_type, half_life in sorted(DEFAULT_TYPES):
            # Use a new UUID as row key.
            row_key = str(uuid.uuid4())
            records.append({
                'PartitionKey': partition_key,
                'RowKey': row_key,
                'type': pharm_type,
                'half_life': half_life,  # New half life parameter (in mins)
                'price': "",
                'time_slots': json.dumps(["anytime"])  # Default time slot stored as JSON.
            })
        try:
            table_manager.upload_batch_to_table(PHARM_TABLE, records)
            existing_records = list(table_manager.query_entities(
                PHARM_TABLE,
                query="PartitionKey eq '{}'".format(partition_key)
            ))
        except Exception as e:
            current_app.logger.error("Failed to prefill pharmaceutical data: %s", e)
            flash("Failed to prefill data. Please check the logs.", "error")

    # Convert JSON time_slots string into a list for each record.
    records = []
    for rec in existing_records:
        if 'time_slots' in rec:
            try:
                rec['time_slots'] = json.loads(rec['time_slots'])
            except Exception:
                rec['time_slots'] = []
        else:
            rec['time_slots'] = []
        records.append(rec)

    return render_template('radiopharmaceutical.html', records=records)


@radiopharm_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_radiopharm():
    """Display form and create a new radiopharmaceutical."""
    partition_key = current_user.username
    table_manager = get_table_manager()

    if request.method == 'POST':
        # Get the submitted form data.
        name = request.form.get('name')
        # Get the half life in minutes.
        half_life = request.form.get('half_life', "")
        price = request.form.get('price', "")
        # For multi-select, getlist returns a list of time slots.
        time_slots = request.form.getlist('time_slots')
        # Generate a unique RowKey.
        row_key = str(uuid.uuid4())

        entity = {
            'PartitionKey': partition_key,
            'RowKey': row_key,
            'type': name,  # Store the pharmaceutical name.
            'half_life': half_life,  # Store the half life (mins)
            'price': price,
            'time_slots': json.dumps(time_slots)
        }
        try:
            table_manager.upload_batch_to_table(PHARM_TABLE, [entity])
            flash("New radiopharmaceutical added successfully.")
        except Exception as e:
            current_app.logger.error("Failed to add radiopharmaceutical: %s", e)
            flash("Failed to add radiopharmaceutical.", "error")
        return redirect(url_for('radiopharm.manage'))

    return render_template('add_radiopharm.html')


@radiopharm_bp.route('/edit/<row_key>', methods=['GET', 'POST'])
@login_required
def edit_radiopharm(row_key):
    """Edit an existing radiopharmaceutical."""
    partition_key = current_user.username
    table_manager = get_table_manager()

    # Retrieve the record.
    record = table_manager.get_entity(PHARM_TABLE, partition_key, row_key)
    if not record:
        flash("Radiopharmaceutical not found.", "error")
        return redirect(url_for('radiopharm.manage'))

    if request.method == 'POST':
        name = request.form.get('name')
        half_life = request.form.get('half_life', "")
        price = request.form.get('price', "")
        time_slots = request.form.getlist('time_slots')

        # Update fields while keeping RowKey unchanged.
        record['type'] = name
        record['half_life'] = half_life  # Update half life
        record['price'] = price
        record['time_slots'] = json.dumps(time_slots)
        try:
            table_manager.upload_batch_to_table(PHARM_TABLE, [record])
            flash("Radiopharmaceutical updated successfully.")
        except Exception as e:
            current_app.logger.error("Failed to update radiopharmaceutical: %s", e)
            flash("Failed to update radiopharmaceutical.", "error")
        return redirect(url_for('radiopharm.manage'))

    # On GET, convert the stored JSON time_slots to a Python list.
    if 'time_slots' in record:
        try:
            record['time_slots'] = json.loads(record['time_slots'])
        except Exception:
            record['time_slots'] = []
    else:
        record['time_slots'] = []

    return render_template('edit_radiopharm.html', record=record)


@radiopharm_bp.route('/delete/<row_key>', methods=['POST'])
@login_required
def delete_radiopharm(row_key):
    """Delete the specified radiopharmaceutical."""
    partition_key = current_user.username
    table_manager = get_table_manager()
    record = table_manager.get_entity(PHARM_TABLE, partition_key, row_key)
    if record:
        try:
            table_manager.delete_entities(PHARM_TABLE, [record])
            flash("Radiopharmaceutical deleted successfully.")
        except Exception as e:
            current_app.logger.error("Failed to delete radiopharmaceutical: %s", e)
            flash("Failed to delete radiopharmaceutical.", "error")
    else:
        flash("Radiopharmaceutical not found.", "error")
    return redirect(url_for('radiopharm.manage'))
