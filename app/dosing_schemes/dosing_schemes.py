import json
import uuid
from flask import render_template, request, redirect, url_for, flash, session, Blueprint
from flask_login import current_user, login_required
from app.constants import DOSING_SCHEMES_TABLE, PHARM_TABLE
from app.table_manager import get_table_manager
from app.radiopharmaceutical.radiopharmaceutical import (
    _get_current_set_name,
    _set_current_set_name,
    _ensure_at_least_one_set,
)

dosing_bp = Blueprint('dosing_schemes', __name__, template_folder='templates')


def _load_set_data(table_mgr, username):
    """Return (current_set, all_set_names, pharm_list, radionuclides)"""
    current = _get_current_set_name(table_mgr, username)
    default = _ensure_at_least_one_set(table_mgr, username)
    if default is None:
        return "", [], []
    if current is None:
        current = default
        try:
            _set_current_set_name(table_mgr, username, current)
        except Exception:
            pass
    else:
        try:
            table_mgr.get_entity(PHARM_TABLE, username, current)
        except Exception:
            current = default
            try:
                _set_current_set_name(table_mgr, username, current)
            except Exception:
                pass

    all_sets_entities = list(table_mgr.query_entities(
        PHARM_TABLE,
        query=f"PartitionKey eq '{username}'"
    ))
    all_names = [ent['RowKey'] for ent in all_sets_entities]

    try:
        ent = table_mgr.get_entity(PHARM_TABLE, username, current)
        pharm_list = json.loads(ent.get('pharm_data', '[]'))
    except Exception:
        pharm_list = []

    radionuclides = sorted({rec.get('radionuclide') for rec in pharm_list if rec.get('radionuclide')})

    return current, all_names, pharm_list, radionuclides


@dosing_bp.route('/dosing_schemes', methods=['GET'])
@login_required
def list_dosing_schemes():
    """Display a list of dosing schemes for the current user."""
    user_id = current_user.username
    table_manager = get_table_manager()

    current_set, all_sets, radiopharms, radionuclides = _load_set_data(table_manager, user_id)

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
            radionuclide = record[1].split('-')[0]
            entity = {
                'PartitionKey': user_id,
                'RowKey': row_key,
                'Name': record[0],
                'Radiopharmaceutical': record[1],
                'Radionuclide': radionuclide,
                'DoseValue': record[2],
                'DoseType': record[3],
                'Uptake1': record[4],
                'Imaging1': record[5],
                'Uptake2': record[6],
                'Imaging2': record[7],
                'SetName': current_set
            }
            batch.append(entity)
        table_manager.upload_batch_to_table(DOSING_SCHEMES_TABLE, batch)
        # Requery after pre-filling.
        schemes = list(table_manager.query_entities(DOSING_SCHEMES_TABLE, query))

    current_set, all_sets, radiopharms, radionuclides = _load_set_data(table_manager, user_id)
    radiopharms_dict = {rec['type']: rec['type'] for rec in radiopharms}
    schemes = [
        {**scheme, 'Radiopharmaceutical': radiopharms_dict.get(scheme['Radiopharmaceutical'], scheme['Radiopharmaceutical'])}
        for scheme in schemes
        if scheme.get('SetName', current_set) == current_set
    ]

    schemes = sorted(schemes, key=lambda x: x['Name'])
    return render_template(
        'dosing_schemes.html',
        schemes=schemes,
        current_set=current_set,
        all_sets=all_sets,
        radiopharmaceuticals=radiopharms,
        radionuclides=radionuclides,
    )


@dosing_bp.route('/dosing_schemes/change_set', methods=['POST'])
@login_required
def change_set():
    """Update the current radiopharmaceutical attribute set pointer."""
    table_mgr = get_table_manager()
    user_id = current_user.username
    selected = request.form.get('attribute_set_selector')

    if not selected:
        flash("No set selected.", "error")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    try:
        table_mgr.get_entity(PHARM_TABLE, user_id, selected)
    except Exception:
        flash("That set no longer exists.", "error")
        return redirect(url_for('dosing_schemes.list_dosing_schemes'))

    try:
        _set_current_set_name(table_mgr, user_id, selected)
        flash(f"Switched to set '{selected}'.", "info")
    except Exception:
        flash("Could not switch sets. Check logs.", "error")

    return redirect(url_for('dosing_schemes.list_dosing_schemes'))


@dosing_bp.route('/dosing_schemes/add', methods=['GET', 'POST'])
@login_required
def add_dosing_scheme():
    """Add a new dosing scheme."""
    user_id = current_user.username
    table_manager = get_table_manager()

    current_set, all_sets, radiopharms, radionuclides = _load_set_data(table_manager, user_id)
    radiopharms = sorted(radiopharms, key=lambda x: x.get('type', ''))

    if request.method == 'POST':
        radionuclide = request.form.get('radionuclide')
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
            'Radionuclide': radionuclide,
            'SetName': current_set,
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
    return render_template(
        'dosing_schemes.html',
        action='add',
        radiopharmaceuticals=radiopharms,
        radionuclides=radionuclides,
        current_set=current_set,
        all_sets=all_sets,
    )


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

    current_set, all_sets, radiopharms, radionuclides = _load_set_data(table_manager, user_id)
    radiopharms = sorted(radiopharms, key=lambda x: x.get('type', ''))

    if request.method == 'POST':
        scheme['Radionuclide'] = request.form.get('radionuclide')
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

    return render_template(
        'dosing_schemes.html',
        action='edit',
        scheme=scheme,
        radiopharmaceuticals=radiopharms,
        radionuclides=radionuclides,
        current_set=current_set,
        all_sets=all_sets,
    )


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
