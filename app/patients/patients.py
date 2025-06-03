from flask import Blueprint, render_template, request, redirect, url_for, flash
import uuid
from flask_login import login_required, current_user
from app.encrypt import get_fernet
import json
from app.constants import (
    DOSING_SCHEMES_TABLE,
    PATIENTS_TABLE,
    DAYSETUP_TABLE,
)
from app.table_manager import get_table_manager


def _time_options():
    """Return a list of time strings in 5 minute intervals."""
    return [f"{h:02d}:{m:02d}" for h in range(7, 18) for m in range(0, 60, 5)]

patients_bp = Blueprint('patients', __name__, template_folder='templates')
fernet = get_fernet()


def _get_current_set_name(table_mgr, username):
    try:
        rec = table_mgr.get_entity(DAYSETUP_TABLE, username, "patient_set")
        return rec.get("value")
    except Exception:
        return None


def _set_current_set_name(table_mgr, username, new_name):
    entity = {
        "PartitionKey": username,
        "RowKey": "patient_set",
        "value": new_name,
    }
    table_mgr.upload_batch_to_table(DAYSETUP_TABLE, [entity])


def _ensure_at_least_one_set(table_mgr, username):
    sets = list(
        table_mgr.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{username}'")
    )
    if not sets:
        entity = {
            "PartitionKey": username,
            "RowKey": "Default",
            "patient_data": json.dumps([]),
        }
        try:
            table_mgr.upload_batch_to_table(PATIENTS_TABLE, [entity])
        except Exception:
            return None
        return "Default"
    return sets[0]["RowKey"]


@login_required
@patients_bp.route('/patients', methods=['GET', 'POST'])
def manage_patients():
    user_id = current_user.username
    table_manager = get_table_manager()

    current_set = _get_current_set_name(table_manager, user_id)
    default_set = _ensure_at_least_one_set(table_manager, user_id)
    if default_set is None:
        return render_template('patients.html', patients=[], dosing_schemes=[],
                               available_radiopharmaceuticals=[],
                               dosing_scheme_by_rowkey={}, time_options=_time_options(),
                               current_set="", all_sets=[])
    if current_set is None:
        current_set = default_set
        try:
            _set_current_set_name(table_manager, user_id, current_set)
        except Exception:
            pass
    else:
        try:
            table_manager.get_entity(PATIENTS_TABLE, user_id, current_set)
        except Exception:
            current_set = default_set
            try:
                _set_current_set_name(table_manager, user_id, current_set)
            except Exception:
                pass

    all_sets_entities = list(
        table_manager.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{user_id}'")
    )
    all_set_names = [ent['RowKey'] for ent in all_sets_entities]

    # Retrieve available dosing schemes for the current user.
    dosing_schemes = list(
        table_manager.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'")
    )
    dosing_scheme_by_rowkey = {scheme['RowKey']: scheme for scheme in dosing_schemes}

    # Compute unique radiopharmaceuticals (those that are associated with a dosing scheme).
    available_rads = sorted(
        list({scheme.get('Radiopharmaceutical') for scheme in dosing_schemes if scheme.get('Radiopharmaceutical')})
    )

    if request.method == 'POST':
        # Add a new patient.
        surname = request.form.get('surname')
        given_name = request.form.get('given_name')
        identification = request.form.get('identification')
        admin_time = request.form.get('admin_time')
        note = request.form.get('note', '')
        immobility = request.form.get('immobility', 'no') == 'yes'
        admin_time = request.form.get('admin_time')
        note = request.form.get('note', '')
        immobility = request.form.get('immobility', 'no') == 'yes'
        try:
            weight = float(request.form.get('weight'))
        except (ValueError, TypeError):
            flash("Weight must be a number.", "error")
            return redirect(url_for('patients.manage_patients'))

        # Retrieve the dosing scheme (selected from the filtered list).
        dosing_scheme_id = request.form.get('dosing_scheme')  # should be the RowKey

        # Retrieve the selected dosing scheme record.
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if not dosing_scheme:
            flash("Selected dosing scheme not found.", "error")
            return redirect(url_for('patients.manage_patients'))

        # Convert the dosing scheme dose value to a float.
        try:
            dose_value = float(dosing_scheme.get('DoseValue', 0))
        except (ValueError, TypeError):
            dose_value = 0

        dose_type = dosing_scheme.get('DoseType', 'fixed')
        if dose_type == 'per_kg':
            administered_dose = dose_value * weight
        else:
            administered_dose = dose_value

        # Encrypt the personal data fields.
        encrypted_surname = fernet.encrypt(surname.encode()).decode()
        encrypted_given_name = fernet.encrypt(given_name.encode()).decode()
        encrypted_identification = fernet.encrypt(identification.encode()).decode()

        patient_entity = {
            'RowKey': str(uuid.uuid4()),
            'Surname': encrypted_surname,
            'GivenName': encrypted_given_name,
            'Identification': encrypted_identification,
            'Weight': weight,
            'DosingSchemeID': dosing_scheme_id,
            'AdministeredDose': administered_dose,
            'AdminTime': admin_time,
            'Note': note,
            'Immobility': immobility,
        }
        try:
            set_entity = table_manager.get_entity(PATIENTS_TABLE, user_id, current_set)
            patient_list = json.loads(set_entity.get('patient_data', '[]'))
        except Exception:
            patient_list = []
        patient_list.append(patient_entity)
        updated_ent = {
            'PartitionKey': user_id,
            'RowKey': current_set,
            'patient_data': json.dumps(patient_list),
        }
        table_manager.upload_batch_to_table(PATIENTS_TABLE, [updated_ent])
        flash("Patient added successfully.", "success")
        return redirect(url_for('patients.manage_patients'))

    # GET request: retrieve patients from the current set JSON blob.
    try:
        set_entity = table_manager.get_entity(PATIENTS_TABLE, user_id, current_set)
        patients_list = json.loads(set_entity.get('patient_data', '[]'))
    except Exception:
        patients_list = []

    # Decrypt personal data before sending to the template.
    for patient in patients_list:
        try:
            patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
            patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
            patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
        except Exception as e:
            patient["Surname"] = "Decryption Error"
            patient["GivenName"] = "Decryption Error"
            patient["Identification"] = "Decryption Error"

    dosing_schemes = sorted(dosing_schemes, key=lambda x: x['Name'])
    return render_template(
        "patients.html",
        dosing_schemes=dosing_schemes,
        available_radiopharmaceuticals=available_rads,
        patients=patients_list,
        dosing_scheme_by_rowkey=dosing_scheme_by_rowkey,
        time_options=_time_options(),
        current_set=current_set,
        all_sets=all_set_names
    )


@login_required
@patients_bp.route('/patients/edit/<int:index>', methods=['GET', 'POST'])
def edit_patient(index):
    user_id = current_user.username
    table_manager = get_table_manager()
    current_set = _get_current_set_name(table_manager, user_id)

    try:
        set_entity = table_manager.get_entity(PATIENTS_TABLE, user_id, current_set)
        patient_list = json.loads(set_entity.get('patient_data', '[]'))
    except Exception:
        flash("Failed to load current set.", "error")
        return redirect(url_for('patients.manage_patients'))

    if index < 0 or index >= len(patient_list):
        flash("Invalid patient index.", "error")
        return redirect(url_for('patients.manage_patients'))

    patient = patient_list[index]

    # Decrypt the patient data.
    try:
        patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
        patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
        patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
    except Exception as e:
        flash("Error decrypting patient data.", "error")
        return redirect(url_for('patients.manage_patients'))

    # Retrieve available dosing schemes for the dropdown.
    dosing_schemes = list(
        table_manager.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'")
    )
    # Compute unique radiopharmaceuticals.
    available_rads = sorted(
        list({scheme.get('Radiopharmaceutical') for scheme in dosing_schemes if scheme.get('Radiopharmaceutical')})
    )
    dosing_schemes = sorted(dosing_schemes, key=lambda x: x['Name'])
    # Determine the radiopharmaceutical corresponding to the patient’s dosing scheme.
    current_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, patient.get('DosingSchemeID'))
    current_rad = current_scheme.get('Radiopharmaceutical') if current_scheme else ""

    if request.method == 'POST':
        # Process the update.
        surname = request.form.get('surname')
        given_name = request.form.get('given_name')
        identification = request.form.get('identification')
        admin_time = request.form.get('admin_time')
        note = request.form.get('note')
        immobility = request.form.get('immobility')
        try:
            weight = float(request.form.get('weight'))
        except (ValueError, TypeError):
            flash("Weight must be a number.", "error")
            return redirect(url_for('patients.edit_patient', index=index))

        dosing_scheme_id = request.form.get('dosing_scheme')
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if not dosing_scheme:
            flash("Selected dosing scheme not found.", "error")
            return redirect(url_for('patients.edit_patient', index=index))

        try:
            dose_value = float(dosing_scheme.get('DoseValue', 0))
        except (ValueError, TypeError):
            dose_value = 0

        dose_type = dosing_scheme.get('DoseType', 'fixed')
        if dose_type == 'per_kg':
            administered_dose = dose_value * weight
        else:
            administered_dose = dose_value

        # Encrypt updated personal data.
        encrypted_surname = fernet.encrypt(surname.encode()).decode()
        encrypted_given_name = fernet.encrypt(given_name.encode()).decode()
        encrypted_identification = fernet.encrypt(identification.encode()).decode()

        # Update the patient record.
        patient['Surname'] = encrypted_surname
        patient['GivenName'] = encrypted_given_name
        patient['Identification'] = encrypted_identification
        patient['Weight'] = weight
        patient['DosingSchemeID'] = dosing_scheme_id
        patient['AdministeredDose'] = administered_dose
        patient['AdminTime'] = admin_time
        patient['Note'] = note
        patient['Immobility'] = immobility

        patient_list[index] = patient
        updated_ent = {
            'PartitionKey': user_id,
            'RowKey': current_set,
            'patient_data': json.dumps(patient_list),
        }
        table_manager.upload_batch_to_table(PATIENTS_TABLE, [updated_ent])
        flash("Patient updated successfully.", "success")
        return redirect(url_for('patients.manage_patients'))

    return render_template(
        "edit_patient.html",
        patient=patient,
        dosing_schemes=dosing_schemes,
        available_radiopharmaceuticals=available_rads,
        current_radiopharmaceutical=current_rad,
        time_options=_time_options(),
        index=index
    )


@login_required
@patients_bp.route('/patients/delete/<int:index>', methods=['POST'])
def delete_patient(index):
    user_id = current_user.username
    table_manager = get_table_manager()
    current_set = _get_current_set_name(table_manager, user_id)

    try:
        set_entity = table_manager.get_entity(PATIENTS_TABLE, user_id, current_set)
        patient_list = json.loads(set_entity.get('patient_data', '[]'))
    except Exception:
        flash("Failed to load current set.", "error")
        return redirect(url_for('patients.manage_patients'))

    if index < 0 or index >= len(patient_list):
        flash("Patient not found.", "error")
        return redirect(url_for('patients.manage_patients'))

    patient_list.pop(index)
    updated_ent = {
        'PartitionKey': user_id,
        'RowKey': current_set,
        'patient_data': json.dumps(patient_list),
    }
    table_manager.upload_batch_to_table(PATIENTS_TABLE, [updated_ent])
    flash("Patient deleted successfully.", "success")
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/clear', methods=['POST'])
def clear_patients():
    user_id = current_user.username
    table_manager = get_table_manager()
    current_set = _get_current_set_name(table_manager, user_id)
    updated_ent = {
        'PartitionKey': user_id,
        'RowKey': current_set,
        'patient_data': json.dumps([]),
    }
    try:
        table_manager.upload_batch_to_table(PATIENTS_TABLE, [updated_ent])
        flash("All patients cleared.", "success")
    except Exception:
        flash("No patients to clear.", "info")
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/change_set', methods=['POST'])
def change_set():
    table_mgr = get_table_manager()
    username = current_user.username
    selected = request.form.get('attribute_set_selector')
    if not selected:
        flash('No set selected.', 'error')
        return redirect(url_for('patients.manage_patients'))
    try:
        table_mgr.get_entity(PATIENTS_TABLE, username, selected)
    except Exception:
        flash('That set no longer exists.', 'error')
        return redirect(url_for('patients.manage_patients'))
    try:
        _set_current_set_name(table_mgr, username, selected)
        flash(f"Switched to set '{selected}'.", 'info')
    except Exception:
        flash('Could not switch sets. Check logs.', 'error')
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/clone_set', methods=['POST'])
def clone_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)
    new_name = request.form.get('new_set_name', '').strip()
    if not new_name:
        flash('New set name cannot be empty.', 'error')
        return redirect(url_for('patients.manage_patients'))
    try:
        maybe = table_mgr.get_entity(PATIENTS_TABLE, username, new_name)
    except Exception:
        maybe = None
    if maybe:
        flash(f"A set named '{new_name}' already exists.", 'error')
        return redirect(url_for('patients.manage_patients'))
    try:
        old_ent = table_mgr.get_entity(PATIENTS_TABLE, username, current)
        blob = old_ent.get('patient_data', '[]')
    except Exception:
        flash('Failed to read current set.', 'error')
        return redirect(url_for('patients.manage_patients'))

    new_entity = {
        'PartitionKey': username,
        'RowKey': new_name,
        'patient_data': blob,
    }
    table_mgr.upload_batch_to_table(PATIENTS_TABLE, [new_entity])
    _set_current_set_name(table_mgr, username, new_name)
    flash(f"Cloned '{current}' → '{new_name}'.", 'success')
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/rename_set', methods=['POST'])
def rename_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)
    new_name = request.form.get('rename_set_name', '').strip()
    if not new_name:
        flash('New set name cannot be empty.', 'error')
        return redirect(url_for('patients.manage_patients'))
    if new_name == current:
        flash('That is already the current set name.', 'info')
        return redirect(url_for('patients.manage_patients'))
    try:
        maybe = table_mgr.get_entity(PATIENTS_TABLE, username, new_name)
    except Exception:
        maybe = None
    if maybe:
        flash(f"A set named '{new_name}' already exists.", 'error')
        return redirect(url_for('patients.manage_patients'))
    try:
        old_ent = table_mgr.get_entity(PATIENTS_TABLE, username, current)
        blob = old_ent.get('patient_data', '[]')
    except Exception:
        flash('Could not load current set data.', 'error')
        return redirect(url_for('patients.manage_patients'))

    new_entity = {
        'PartitionKey': username,
        'RowKey': new_name,
        'patient_data': blob,
    }
    try:
        table_mgr.upload_batch_to_table(PATIENTS_TABLE, [new_entity])
    except Exception as e:
        flash('Could not rename (create new).', 'error')
        return redirect(url_for('patients.manage_patients'))

    try:
        table_mgr.delete_entities(PATIENTS_TABLE, [old_ent])
    except Exception:
        flash('Renamed new set, but failed to delete old.', 'warning')

    _set_current_set_name(table_mgr, username, new_name)
    flash(f"Renamed '{current}' → '{new_name}'.", 'success')
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/delete_set', methods=['POST'])
def delete_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)
    if not current:
        flash('No current set found.', 'error')
        return redirect(url_for('patients.manage_patients'))
    all_sets = list(table_mgr.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{username}'"))
    if len(all_sets) <= 1:
        flash('Cannot delete the only set.', 'error')
        return redirect(url_for('patients.manage_patients'))
    current_ent = table_mgr.get_entity(PATIENTS_TABLE, username, current)
    table_mgr.delete_entities(PATIENTS_TABLE, [current_ent])
    remaining = [ent['RowKey'] for ent in all_sets if ent['RowKey'] != current]
    new_current = remaining[0] if remaining else None
    if new_current:
        _set_current_set_name(table_mgr, username, new_current)
    flash(f"Deleted set '{current}'.", 'success')
    return redirect(url_for('patients.manage_patients'))
