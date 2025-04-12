import uuid
from flask import render_template, request, redirect, url_for, flash, session, Blueprint
from flask_login import current_user, login_required

from app.constants import DOSING_SCHEMES_TABLE
from app.table_manager import get_table_manager

dosing_bp = Blueprint('dosing_schemes', __name__, template_folder='templates')


@dosing_bp.route('/dosing_schemes', methods=['GET'])
@login_required
def list_dosing_schemes():
    """Display a list of dosing schemes for the current user."""
    user_id = current_user.username

    table_manager = get_table_manager()
    # Query using the user id as PartitionKey.
    query = f"PartitionKey eq '{user_id}'"
    schemes = list(table_manager.query_entities(DOSING_SCHEMES_TABLE, query))

    # Prefill default records if none are found.
    if not schemes:
        prefill_data = [
            # (Name, DoseValue, DoseType, Uptake1, Imaging1, Uptake2, Imaging2)
            ("18FDG (onko aj)", 2.5, "per_kg", 60, 25, 0, 0),
            ("18FDG (mozek)", 150, "fixed", 0, 60, 0, 0),
            ("18F-Vizamyl", 185, "fixed", 90, 20, 0, 0),
            ("68Ga-SomaKit", 1.85, "per_kg", 60, 30, 0, 0),
            ("68Ga-PSMA", 2, "per_kg", 60, 30, 0, 0),
            ("11C-methionin", 4.5, "per_kg", 0, 20, 90, 20)
        ]
        batch = []
        for record in prefill_data:
            row_key = str(uuid.uuid4())
            entity = {
                'PartitionKey': user_id,
                'RowKey': row_key,
                'Name': record[0],
                'DoseValue': record[1],
                'DoseType': record[2],
                'Uptake1': record[3],
                'Imaging1': record[4],
                'Uptake2': record[5],
                'Imaging2': record[6]
            }
            batch.append(entity)
        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, batch)
        # Requery after pre-filling.
        schemes = list(table_manager.query_entities(DOSING_SCHEMES_TABLE, query))

    return render_template('dosing_schemes.html', schemes=sorted(schemes, key=lambda x: x['Name']))


@dosing_bp.route('/dosing_schemes/add', methods=['GET', 'POST'])
@login_required
def add_dosing_scheme():
    """Add a new dosing scheme."""
    user_id = current_user.username

    if request.method == 'POST':
        name = request.form.get('name')
        # Convert the dose value to a float.
        try:
            dose_value = float(request.form.get('dose_value'))
        except (ValueError, TypeError):
            flash("Dose value must be a number.", "error")
            return redirect(url_for('dosing_schemes.add_dosing_scheme'))
        dose_type = request.form.get('dose_type')
        uptake1 = request.form.get('uptake1')
        imaging1 = request.form.get('imaging1')
        uptake2 = request.form.get('uptake2', 0)
        imaging2 = request.form.get('imaging2', 0)

        row_key = str(uuid.uuid4())
        entity = {
            'PartitionKey': user_id,
            'RowKey': row_key,
            'Name': name,
            'DoseValue': dose_value,
            'DoseType': dose_type,
            'Uptake1': uptake1,
            'Imaging1': imaging1,
            'Uptake2': uptake2,
            'Imaging2': imaging2
        }
        table_manager = get_table_manager()
        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, [entity])

        flash("Dosing scheme added successfully.", "success")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    # When GET request, render the form in "add" mode.
    return render_template('dosing_schemes.html', action='add')


@dosing_bp.route('/dosing_schemes/edit/<row_key>', methods=['GET', 'POST'])
@login_required
def edit_dosing_scheme(row_key):
    """Edit an existing dosing scheme."""
    user_id = current_user.username

    table_manager = get_table_manager()
    scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, row_key)
    if not scheme:
        flash("Dosing scheme not found.", "error")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    if request.method == 'POST':
        scheme['Name'] = request.form.get('name')
        try:
            scheme['DoseValue'] = float(request.form.get('dose_value'))
        except (ValueError, TypeError):
            flash("Dose value must be a number.", "error")
            return redirect(url_for('dosing_schemes.edit_dosing_scheme', row_key=row_key))
        scheme['DoseType'] = request.form.get('dose_type')
        scheme['Uptake1'] = request.form.get('uptake1')
        scheme['Imaging1'] = request.form.get('imaging1')
        scheme['Uptake2'] = request.form.get('uptake2', 0)
        scheme['Imaging2'] = request.form.get('imaging2', 0)

        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, [scheme])
        flash("Dosing scheme updated successfully.", "success")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    return render_template('dosing_schemes.html', action='edit', scheme=scheme)


@dosing_bp.route('/dosing_schemes/delete/<row_key>', methods=['POST'])
@login_required
def delete_dosing_scheme(row_key):
    """Delete a dosing scheme."""
    user_id = current_user.username

    table_manager = get_table_manager()
    scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, row_key)
    if scheme:
        table_manager.delete_entities(DOSING_SCHEMES_TABLE, [scheme])
        flash("Dosing scheme deleted successfully.", "success")
    else:
        flash("Dosing scheme not found.", "error")

    return redirect(url_for('dosing_schemes.list_dosing_schemes'))
