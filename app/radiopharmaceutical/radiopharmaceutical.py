import logging
import json
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.constants import PHARM_TABLE, DAYSETUP_TABLE, RADIONUCLIDE_TABLE
from app.table_manager import get_table_manager

radiopharm_bp = Blueprint('radiopharm', __name__, template_folder='templates')

DEFAULT_TYPES = [
    ('FDG', '18F'),
    ('PSMA', '18F'),
    ('FET', '18F'),
    ('Cholin', '18F'),
    ('NaF', '18F'),
    ('FDOPA', '18F'),
    ('Vizamyl (fluemetamol)', '18F'),
    ('DOTATOC', '68Ga'),
    ('PSMA-11', '68Ga'),
    ('FAPI', '68Ga'),
    ('Cholin', '11C'),
    ('Methionin', '11C'),
    ('H2O', '15O'),
    ('NH3', '13N'),
]


def _get_current_set_name(table_mgr, username):
    """
    Fetch the pointer from DaySetupTable.
    Return None if it doesn’t exist.
    """
    try:
        rec = table_mgr.get_entity(DAYSETUP_TABLE, username, "radiopharmaceutical_attribute_set")
        return rec.get('value')
    except Exception:
        return None


def _set_current_set_name(table_mgr, username, new_set_name):
    """
    Create or update the pointer in DaySetupTable so that
    RowKey="radiopharmaceutical_attribute_set" → value=<new_set_name>.
    """
    entity = {
        'PartitionKey': username,
        'RowKey': "radiopharmaceutical_attribute_set",
        'value': new_set_name
    }
    # Try to upload (this will create or overwrite).
    try:
        table_mgr.upload_batch_to_table(DAYSETUP_TABLE, [entity])
    except Exception as e:
        current_app.logger.error("Failed to set current attribute set: %s", e)
        raise


def _ensure_at_least_one_set(table_mgr, username):
    """
    If the user has no attribute‐sets in PHARM_TABLE, create one called "Default".
    Returns the name of the one-and-only set.
    """
    # Query for any existing sets:
    existing_sets = list(table_mgr.query_entities(
        PHARM_TABLE,
        query=f"PartitionKey eq '{username}'"
    ))

    if not existing_sets:
        # Nothing exists → build a single "Default" set (rowkey = "Default")
        default_list = []
        for pharm_type, nuclide in sorted(DEFAULT_TYPES):
            try:
                rad = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, nuclide)
                hl = rad.get('half_life')
            except Exception:
                hl = ""
            default_list.append({
                'type': pharm_type,
                'radionuclide': nuclide,
                'half_life': hl,
                'price': "",
                'time_slots': ["anytime"],
                'qc_amount': "",
                'qc_unit': "percent",
                'qc_time': ""
            })
        new_entity = {
            'PartitionKey': username,
            'RowKey': "Default",
            'pharm_data': json.dumps(default_list)
        }
        try:
            table_mgr.upload_batch_to_table(PHARM_TABLE, [new_entity])
        except Exception as e:
            current_app.logger.error("Failed to create Default attribute set: %s", e)
            flash("Could not create default set. Check logs.", "error")
            return None
        return "Default"
    else:
        # At least one set already exists. Return the first one’s RowKey.
        return existing_sets[0]['RowKey']


@radiopharm_bp.route('/manage', methods=['GET'])
@login_required
def manage():
    """
    1) Check DaySetupTable for "radiopharmaceutical_attribute_set". If missing, initialize it to "Default".
    2) Ensure there is at least one set in PHARM_TABLE (named "Default" if none existed).
    3) Load the pointer from DaySetupTable to know which set is 'current'.
    4) Load that set’s pharm_data JSON and render it.
    """
    table_mgr = get_table_manager()
    username = current_user.username

    # ── 1) Check DaySetupTable for our pointer. If it doesn't exist, we’ll create "Default" below. ──
    pointer = _get_current_set_name(table_mgr, username)
    if pointer is None:
        # No pointer found at all → we’ll set it to "Default" once the "Default" set exists.
        pointer = None

    # ── 2) Ensure at least one attribute‐set exists in PHARM_TABLE; get its name. ──
    default_set_name = _ensure_at_least_one_set(table_mgr, username)
    if default_set_name is None:
        # If we failed to create a default, just bail out with an empty page.
        return render_template('radiopharmaceutical.html',
                               current_set="",
                               all_sets=[],
                               records=[])

    # If there was no pointer, or if it pointed to something that no longer exists, reset to default
    if pointer is None:
        pointer = default_set_name
        try:
            _set_current_set_name(table_mgr, username, pointer)
        except Exception:
            # Log already happened in helper; continue anyway
            pass
    else:
        # If pointer exists but the referenced set is gone, reset it.
        try:
            _ = table_mgr.get_entity(PHARM_TABLE, username, pointer)
        except Exception:
            # The set named <pointer> is missing. Reset to default_set_name.
            pointer = default_set_name
            try:
                _set_current_set_name(table_mgr, username, pointer)
            except Exception:
                pass

    # ── 3) Now 'pointer' is guaranteed to be a valid set name. Fetch all set-names. ──
    all_sets_entities = list(table_mgr.query_entities(
        PHARM_TABLE,
        query=f"PartitionKey eq '{username}'"
    ))
    all_set_names = [ent['RowKey'] for ent in all_sets_entities]

    # ── 4) Load the JSON blob for the current set, parse it. ──
    try:
        set_entity = table_mgr.get_entity(PHARM_TABLE, username, pointer)
        raw_json = set_entity.get('pharm_data', '[]')
        pharm_list = json.loads(raw_json)
    except Exception:
        pharm_list = []

    # Ensure 'time_slots' is a list and QC fields exist
    for item in pharm_list:
        if 'time_slots' in item and not isinstance(item['time_slots'], list):
            try:
                item['time_slots'] = json.loads(item['time_slots'])
            except Exception:
                item['time_slots'] = []
        # lookup half-life from radionuclide table if possible
        rad_name = item.get('radionuclide')
        if rad_name:
            try:
                rad = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, rad_name)
                item['half_life'] = rad.get('half_life')
            except Exception:
                pass
        item.setdefault('qc_amount', "")
        item.setdefault('qc_unit', "percent")
        item.setdefault('qc_time', "")

    return render_template(
        'radiopharmaceutical.html',
        current_set=pointer,
        all_sets=all_set_names,
        records=pharm_list
    )


@radiopharm_bp.route('/manage/change_set', methods=['POST'])
@login_required
def change_set():
    """
    User picked a different set from the dropdown.
    Update DaySetupTable so that current = selected.
    """
    table_mgr = get_table_manager()
    username = current_user.username
    selected = request.form.get('attribute_set_selector')

    if not selected:
        flash("No set selected.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Verify it actually exists
    try:
        _ = table_mgr.get_entity(PHARM_TABLE, username, selected)
    except Exception:
        flash("That set no longer exists.", "error")
        return redirect(url_for('radiopharm.manage'))

    try:
        _set_current_set_name(table_mgr, username, selected)
        flash(f"Switched to set '{selected}'.", "info")
    except Exception:
        flash("Could not switch sets. Check logs.", "error")

    return redirect(url_for('radiopharm.manage'))


@radiopharm_bp.route('/manage/clone_set', methods=['POST'])
@login_required
def clone_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)
    new_name = request.form.get('new_set_name', "").strip()
    if not new_name:
        flash("New set name cannot be empty.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Make sure new_name doesn’t already exist
    try:
        maybe = table_mgr.get_entity(PHARM_TABLE, username, new_name)
    except Exception:
        maybe = None

    if maybe:
        flash(f"A set named '{new_name}' already exists.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Copy JSON from current
    try:
        old_ent = table_mgr.get_entity(PHARM_TABLE, username, current)
        blob = old_ent.get('pharm_data', '[]')
    except Exception:
        flash("Failed to read current set.", "error")
        return redirect(url_for('radiopharm.manage'))

    new_entity = {
        'PartitionKey': username,
        'RowKey': new_name,
        'pharm_data': blob
    }
    try:
        table_mgr.upload_batch_to_table(PHARM_TABLE, [new_entity])
        _set_current_set_name(table_mgr, username, new_name)
        flash(f"Cloned '{current}' → '{new_name}'.", "success")
    except Exception as e:
        current_app.logger.error("Failed to clone set: %s", e)
        flash("Could not clone set. Check logs.", "error")

    return redirect(url_for('radiopharm.manage'))


@radiopharm_bp.route('/manage/rename_set', methods=['POST'])
@login_required
def rename_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)
    new_name = request.form.get('rename_set_name', "").strip()
    if not new_name:
        flash("New set name cannot be empty.", "error")
        return redirect(url_for('radiopharm.manage'))

    if new_name == current:
        flash("That is already the current set name.", "info")
        return redirect(url_for('radiopharm.manage'))

    # Ensure new_name isn’t already taken
    try:
        maybe = table_mgr.get_entity(PHARM_TABLE, username, new_name)
    except Exception:
        maybe = None

    if maybe:
        flash(f"A set named '{new_name}' already exists.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Read old entity
    try:
        old_ent = table_mgr.get_entity(PHARM_TABLE, username, current)
        blob = old_ent.get('pharm_data', '[]')
    except Exception:
        flash("Could not load current set data.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Create new entity
    new_entity = {
        'PartitionKey': username,
        'RowKey': new_name,
        'pharm_data': blob
    }
    try:
        table_mgr.upload_batch_to_table(PHARM_TABLE, [new_entity])
    except Exception as e:
        current_app.logger.error("Failed to create renamed set: %s", e)
        flash("Could not rename (create new).", "error")
        return redirect(url_for('radiopharm.manage'))

    # Delete old entity
    try:
        table_mgr.delete_entities(PHARM_TABLE, [old_ent])
    except Exception as e:
        current_app.logger.error("Failed to delete old set: %s", e)
        flash("Renamed new set, but failed to delete old. Remove manually if needed.", "warning")

    # Update pointer
    try:
        _set_current_set_name(table_mgr, username, new_name)
    except Exception:
        flash("Renamed set, but failed to update pointer. Check logs.", "error")

    flash(f"Renamed '{current}' → '{new_name}'.", "success")
    return redirect(url_for('radiopharm.manage'))


@radiopharm_bp.route('/manage/delete_set', methods=['POST'])
@login_required
def delete_set():
    table_mgr = get_table_manager()
    username = current_user.username
    current = _get_current_set_name(table_mgr, username)

    if not current:
        flash("No current set found.", "error")
        return redirect(url_for('radiopharm.manage'))

    # Fetch all existing sets for this user
    all_sets = list(table_mgr.query_entities(
        PHARM_TABLE,
        query=f"PartitionKey eq '{username}'"
    ))

    if len(all_sets) <= 1:
        flash("Cannot delete the only set.", "error")
        return redirect(url_for('radiopharm.manage'))

    try:
        current_ent = table_mgr.get_entity(PHARM_TABLE, username, current)
    except Exception:
        flash("Could not load current set.", "error")
        return redirect(url_for('radiopharm.manage'))

    try:
        table_mgr.delete_entities(PHARM_TABLE, [current_ent])
    except Exception as e:
        current_app.logger.error("Failed to delete set: %s", e)
        flash("Could not delete set.", "error")
        return redirect(url_for('radiopharm.manage'))

    remaining_names = [ent['RowKey'] for ent in all_sets if ent['RowKey'] != current]
    new_current = remaining_names[0] if remaining_names else None
    try:
        if new_current:
            _set_current_set_name(table_mgr, username, new_current)
    except Exception:
        flash("Deleted set, but failed to update pointer.", "warning")

    flash(f"Deleted set '{current}'.", "success")
    return redirect(url_for('radiopharm.manage'))


@radiopharm_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_radiopharm():
    table_mgr = get_table_manager()
    username = current_user.username
    current_set = _get_current_set_name(table_mgr, username)
    if not current_set:
        flash("No current set found.", "error")
        return redirect(url_for('radiopharm.manage'))

    radionuclides = list(table_mgr.query_entities(RADIONUCLIDE_TABLE, f"PartitionKey eq '{username}'"))
    radionuclides = sorted(radionuclides, key=lambda r: r['RowKey'])

    if request.method == 'POST':
        name = request.form.get('name')
        radionuclide = request.form.get('radionuclide')
        try:
            rad = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, radionuclide)
            half_life = rad.get('half_life')
        except Exception:
            half_life = ""
        price = request.form.get('price', "")
        time_slots = request.form.getlist('time_slots')
        qc_amount = request.form.get('qc_amount', "")
        qc_unit = request.form.get('qc_unit', "percent")
        qc_time = request.form.get('qc_time', "")

        try:
            ent = table_mgr.get_entity(PHARM_TABLE, username, current_set)
            pharm_list = json.loads(ent.get('pharm_data', '[]'))
        except Exception:
            pharm_list = []

        pharm_list.append({
            'type': name,
            'radionuclide': radionuclide,
            'half_life': half_life,
            'price': price,
            'time_slots': time_slots,
            'qc_amount': qc_amount,
            'qc_unit': qc_unit,
            'qc_time': qc_time
        })

        updated_ent = {
            'PartitionKey': username,
            'RowKey': current_set,
            'pharm_data': json.dumps(pharm_list)
        }
        try:
            table_mgr.upload_batch_to_table(PHARM_TABLE, [updated_ent])
            flash("New radiopharmaceutical added to set.", "success")
        except Exception as e:
            current_app.logger.error("Failed to add item: %s", e)
            flash("Could not add radiopharmaceutical.", "error")

        return redirect(url_for('radiopharm.manage'))

    return render_template('add_radiopharm.html', radionuclides=radionuclides)


@radiopharm_bp.route('/edit/<int:index>', methods=['GET', 'POST'])
@login_required
def edit_radiopharm(index):
    table_mgr = get_table_manager()
    username = current_user.username
    current_set = _get_current_set_name(table_mgr, username)

    try:
        ent = table_mgr.get_entity(PHARM_TABLE, username, current_set)
        pharm_list = json.loads(ent.get('pharm_data', '[]'))
    except Exception:
        flash("Failed to load current set.", "error")
        return redirect(url_for('radiopharm.manage'))

    if index < 0 or index >= len(pharm_list):
        flash("Invalid item index.", "error")
        return redirect(url_for('radiopharm.manage'))

    radionuclides = list(table_mgr.query_entities(RADIONUCLIDE_TABLE, f"PartitionKey eq '{username}'"))
    radionuclides = sorted(radionuclides, key=lambda r: r['RowKey'])

    if request.method == 'POST':
        name = request.form.get('name')
        radionuclide = request.form.get('radionuclide')
        try:
            rad = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, radionuclide)
            half_life = rad.get('half_life')
        except Exception:
            half_life = ""
        price = request.form.get('price', "")
        time_slots = request.form.getlist('time_slots')
        qc_amount = request.form.get('qc_amount', "")
        qc_unit = request.form.get('qc_unit', "percent")
        qc_time = request.form.get('qc_time', "")

        pharm_list[index] = {
            'type': name,
            'radionuclide': radionuclide,
            'half_life': half_life,
            'price': price,
            'time_slots': time_slots,
            'qc_amount': qc_amount,
            'qc_unit': qc_unit,
            'qc_time': qc_time
        }

        updated_ent = {
            'PartitionKey': username,
            'RowKey': current_set,
            'pharm_data': json.dumps(pharm_list)
        }
        try:
            table_mgr.upload_batch_to_table(PHARM_TABLE, [updated_ent])
            flash("Radiopharmaceutical updated.", "success")
        except Exception as e:
            current_app.logger.error("Failed to update item: %s", e)
            flash("Could not update radiopharmaceutical.", "error")

        return redirect(url_for('radiopharm.manage'))

    record = pharm_list[index]
    record.setdefault('qc_amount', "")
    record.setdefault('qc_unit', "percent")
    record.setdefault('qc_time', "")
    return render_template('edit_radiopharm.html', record=record, index=index, radionuclides=radionuclides)


@radiopharm_bp.route('/delete/<int:index>', methods=['POST'])
@login_required
def delete_radiopharm(index):
    table_mgr = get_table_manager()
    username = current_user.username
    current_set = _get_current_set_name(table_mgr, username)

    try:
        ent = table_mgr.get_entity(PHARM_TABLE, username, current_set)
        pharm_list = json.loads(ent.get('pharm_data', '[]'))
    except Exception:
        flash("Failed to load current set.", "error")
        return redirect(url_for('radiopharm.manage'))

    if index < 0 or index >= len(pharm_list):
        flash("Invalid index.", "error")
        return redirect(url_for('radiopharm.manage'))

    pharm_list.pop(index)
    updated_ent = {
        'PartitionKey': username,
        'RowKey': current_set,
        'pharm_data': json.dumps(pharm_list)
    }
    try:
        table_mgr.upload_batch_to_table(PHARM_TABLE, [updated_ent])
        flash("Radiopharmaceutical removed.", "success")
    except Exception as e:
        current_app.logger.error("Failed to delete item: %s", e)
        flash("Could not delete radiopharmaceutical.", "error")

    return redirect(url_for('radiopharm.manage'))
