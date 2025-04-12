import uuid
from flask import render_template, request, redirect, url_for, flash, session, Blueprint
from flask_login import current_user, login_required
from app.constants import DOSING_SCHEMES_TABLE, PHARM_TABLE
from app.table_manager import get_table_manager

dosing_bp = Blueprint('dosing_schemes', __name__, template_folder='templates')


@dosing_bp.route('/dosing_schemes', methods=['GET'])
@login_required
def list_dosing_schemes():
    """Display a list of dosing schemes for the current user."""
    user_id = current_user.username
    table_manager = get_table_manager()
    # Query dosing schemes using the user id as PartitionKey.
    query = f"PartitionKey eq '{user_id}'"
    schemes = list(table_manager.query_entities(DOSING_SCHEMES_TABLE, query))

    # Prefill default records if none are found.
    if not schemes:
        prefill_data = [
            # (Name, Radiopharmaceutical, DoseValue, DoseType, Uptake1, Imaging1, Uptake2, Imaging2)
            ("onko aj", "18F-FDG", 2.5, "per_kg", 60, 25, 0, 0),
            ("mozek", "18F-FDG", 150, "fixed", 0, 60, 0, 0),
            ("amyloid mozek", "18F-Vizamyl (fluemetamol)", 185, "fixed", 90, 20, 0, 0),
            ("neuroendokrinni tumory", "68Ga-PSMA-11", 1.85, "per_kg", 60, 30, 0, 0),
            ("karcinom prostaty", "68Ga-PSMA-11", 2, "per_kg", 60, 30, 0, 0),
            ("mozkove nadory", "11C-Cholin", 4.5, "per_kg", 0, 20, 90, 20)
        ]
        batch = []
        for record in prefill_data:
            row_key = str(uuid.uuid4())
            entity = {
                'PartitionKey': user_id,
                'RowKey': row_key,
                'Name': record[0],
                'Radiopharmaceutical': record[1],
                'DoseValue': record[2],
                'DoseType': record[3],
                'Uptake1': record[4],
                'Imaging1': record[5],
                'Uptake2': record[6],
                'Imaging2': record[7]
            }
            batch.append(entity)
        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, batch)
        # Requery after pre-filling.
        schemes = list(table_manager.query_entities(DOSING_SCHEMES_TABLE, query))

    # Get list of radiopharmaceuticals for the user from PHARM_TABLE.
    radiopharms = list(table_manager.query_entities(PHARM_TABLE, f"PartitionKey eq '{user_id}'"))
    radiopharms_dict = {rec['RowKey']: rec['type'] for rec in radiopharms}
    schemes = [
        {**scheme, 'Radiopharmaceutical': radiopharms_dict.get(scheme['Radiopharmaceutical'], scheme['Radiopharmaceutical'])}
        for scheme in schemes
    ]

    schemes = sorted(schemes, key=lambda x: x['Name'])
    return render_template('dosing_schemes.html', schemes=schemes)


@dosing_bp.route('/dosing_schemes/add', methods=['GET', 'POST'])
@login_required
def add_dosing_scheme():
    """Add a new dosing scheme."""
    user_id = current_user.username
    table_manager = get_table_manager()

    # Get list of radiopharmaceuticals for the user from PHARM_TABLE.
    radiopharms = list(table_manager.query_entities(PHARM_TABLE, f"PartitionKey eq '{user_id}'"))
    # Sort radiopharmaceuticals by the 'type' field.
    radiopharms = sorted(radiopharms, key=lambda x: x.get('type', ''))

    if request.method == 'POST':
        radiopharmaceutical = request.form.get('radiopharmaceutical')
        name = request.form.get('name')
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
            'Radiopharmaceutical': radiopharmaceutical,
            'DoseValue': dose_value,
            'DoseType': dose_type,
            'Uptake1': uptake1,
            'Imaging1': imaging1,
            'Uptake2': uptake2,
            'Imaging2': imaging2
        }
        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, [entity])

        flash("Dosing scheme added successfully.", "success")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    # When GET request, render the form in "add" mode.
    return render_template('dosing_schemes.html', action='add', radiopharmaceuticals=radiopharms)


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

    # Get list of radiopharmaceuticals for the user.
    radiopharms = list(table_manager.query_entities(PHARM_TABLE, f"PartitionKey eq '{user_id}'"))
    radiopharms = sorted(radiopharms, key=lambda x: x.get('type', ''))

    if request.method == 'POST':
        scheme['Radiopharmaceutical'] = request.form.get('radiopharmaceutical')
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

    return render_template('dosing_schemes.html', action='edit', scheme=scheme, radiopharmaceuticals=radiopharms)


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
