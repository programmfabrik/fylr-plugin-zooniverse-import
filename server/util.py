# coding=utf8

import json
import re
import requests
from datetime import datetime, timedelta

import zooniverse
import mapping

from fylr_lib_plugin_python3 import util as fylr_util


# fylr collects everything the plugin writes to stderr in the server log.
# debug logging is off by default, it writes one line per parsed record.
DEBUG = False


# ---------------------
__times = {}


def time_now(k):
    __times[k] = datetime.now()
    return __times[k]


def time_diff(k):
    if k not in __times:
        return None

    sec = timedelta.total_seconds(datetime.now() - __times[k])
    return sec * 1000.0


# ---------------------


def debug(line):
    if DEBUG:
        fylr_util.stderr('[debug] ' + line)


def info(line):
    fylr_util.stderr('[info] ' + line)


def warn(line):
    fylr_util.stderr('[warn] ' + line)


# ---------------------


def dumpjs(js, indent=4):
    return json.dumps(js, indent=indent)


def get_json_value(js, path, expected=False, split_char='.'):
    current = js
    path_parts = []
    current_part = ''

    for i in range(len(path)):
        if path[i] != split_char:
            current_part += path[i]
            if i == len(path) - 1:
                path_parts.append(current_part)
            continue

        if i > 0 and path[i - 1] == '\\':
            current_part += path[i]
            continue

        if len(current_part) > 0:
            path_parts.append(current_part)
            current_part = ''

    for path_part in path_parts:
        path_part = path_part.replace('\\' + split_char, split_char)

        if not isinstance(current, dict) or path_part not in current:
            if expected:
                raise Exception('expected: ' + path_part)
            else:
                return None

        current = current[path_part]

    return current


# ---------------------


def return_error_response(msg):
    # the error code is translated in the webfrontend, the message is shown as
    # the "description" parameter
    fylr_util.return_error_response_with_parameters(
        error_code='error.user.zooniverse_import',
        error_msg=msg,
        parameters={'description': msg},
    )


# ---------------------


def load_mappings(plugin_config, datamodel_columns):
    # debug('plugin_config: {0}'.format(dumpjs(plugin_config)))

    mappings = {}

    if not isinstance(plugin_config, list):
        return mappings

    for m in plugin_config:
        enabled = get_json_value(m, 'enabled')
        if not isinstance(enabled, bool):
            continue
        if not enabled:
            continue

        update_objecttype = get_json_value(m, 'update_objecttype')
        if not isinstance(update_objecttype, str):
            continue
        if len(update_objecttype) < 1:
            continue

        match_column = get_json_value(m, 'match_column')
        if not isinstance(match_column, str):
            continue
        if not match_column.startswith('{0}.'.format(update_objecttype)):
            continue
        m['match_column'] = match_column[len(update_objecttype) + 1 :]

        for k in m.keys():
            if not k.startswith('update_column_'):
                continue

            update_column = m[k]
            if update_column not in datamodel_columns:
                continue

            # debug('column {0}: {1}'.format(update_column, dumpjs(datamodel_columns[update_column])))
            m[k] = datamodel_columns[update_column]

        mappings[update_objecttype] = m

    return mappings


# ---------------------------------


def __search(api_url, token, query) -> dict:
    resp = requests.post(
        api_url + '/search',
        headers={
            'Authorization': 'Bearer ' + token,
        },
        data=dumpjs(query),
    )

    if resp.status_code != 200:
        raise Exception(
            'search error: status: {0}, response: {1}'.format(
                resp.status_code, resp.text
            )
        )

    try:
        result = json.loads(resp.text)
    except Exception as e:
        raise Exception(
            'search error: could not parse search response {0}: {1}'.format(
                resp.text, str(e)
            )
        )

    return result


# ---------------------------------


def __load_objects_by_signature(
    objecttype,
    match_column,
    signatures,
    api_url,
    token,
):
    limit = 1000
    offset = 0

    affected_objects = {}

    has_more = True
    while has_more:
        query = {
            'offset': 0,
            'limit': limit,
            'search': [
                {
                    'bool': 'must',
                    'type': 'in',
                    'fields': ['{0}.{1}'.format(objecttype, match_column)],
                    'in': signatures[offset : offset + limit],
                }
            ],
            'format': 'long',
            'objecttypes': [objecttype],
        }
        offset += limit
        has_more = offset < len(signatures)

        result = __search(api_url, token, query)

        result_objects = get_json_value(result, 'objects')

        if not isinstance(result_objects, list):
            raise Exception('Could not parse objects array from search result')

        for obj in result_objects:
            signatur = get_json_value(obj, '{0}.{1}'.format(objecttype, match_column))
            if not isinstance(signatur, str):
                continue

            obj = get_json_value(obj, objecttype)
            if not isinstance(obj, dict):
                continue

            affected_objects[signatur] = obj

    return affected_objects


# -----------------------


def __create_new_linked_objects(
    unique_linked_object_values,
    languages,
    api_url,
    token,
):
    new_linked_objects = {}
    count_objects = {}

    for objecttype in unique_linked_object_values:
        new_linked_objects[objecttype] = []
        count = 0

        for match_column in unique_linked_object_values[objecttype]:
            unique_values = unique_linked_object_values[objecttype][match_column]
            # debug('[create_new_linked_objects] field: {0}.{1}, values: {2}'.format(objecttype, match_column, unique_values))

            existing_objects = __load_objects_by_signature(
                objecttype,
                match_column,
                unique_values,
                api_url,
                token,
            )
            # debug('[create_new_linked_objects] existing objects for values: {0}' .format(existing_objects.keys()))

            # for all unique values, skip those which are found and create new objects for all missing values
            for v in unique_values:
                if v in existing_objects:
                    continue

                # create a new object
                new_linked_objects[objecttype].append(
                    __build_object(
                        objecttype, {'_version': 1, match_column: v}, languages
                    )
                )
                count += 1

        if count > 0:
            count_objects[objecttype] = count

    return new_linked_objects, count_objects


# -----------------------


def __build_object(objecttype, obj, languages):
    commments_l10n = {
        'de-DE': 'Bearbeitung erfolgte durch das Zooniverse Import Plugin',
        'en-US': 'Object was edited by the Zooniverse Import Plugin',
    }
    comment = commments_l10n['en-US']
    if len(languages) > 0 and languages[0] in commments_l10n:
        comment = commments_l10n[languages[0]]

    return {
        '_comment': comment,
        '_mask': '_all_fields',
        '_objecttype': objecttype,
        objecttype: obj,
    }


def __check_datetime(value):
    try:
        m = re.match(
            r'^\d{4}(-\d{2}(-\d{2}((T| )(\d{2}(:\d{2}(:\d{2}((\+|-)\d{1,2}:\d{2}){0,1}){0,1}){0,1}){0,1}){0,1}){0,1}){0,1}$',
            value,
        )
        return m is not None
    except:
        return False


def format_datetime(value):
    if not __check_datetime(value):
        return None

    return {'value': value}


def format_date(value):
    if not __check_datetime(value):
        return None

    return {'value': re.split(' |T', value)[0].strip()}


def split_value(value):
    res = []
    if isinstance(value, str):
        parts = re.split(',|;', value)
        for p in parts:
            p = p.strip()
            if len(p) < 1:
                continue
            res.append(p)

        if len(res) < 1:
            return None
        return res

    if isinstance(value, list):
        for v in value:
            parts = re.split(',|;', v)
            for p in parts:
                p = p.strip()
                if len(p) < 1:
                    continue
                res.append(p)

        if len(res) < 1:
            return None
        return res

    return None


# ---------------------------


def parse_datamodel(data):
    if data is None:
        return {}

    js = None
    try:
        js = json.loads(data)
    except:
        return {}

    datamodel_columns = get_json_value(js, 'datamodel_columns')
    if not isinstance(datamodel_columns, dict):
        return {}

    return datamodel_columns


# ---------------------------


def import_data(
    post_body,
    mappings,
    languages,
    api_url,
    token,
) -> dict:
    start_all = time_now('start_all')
    time_now('start_parse_data')
    collected_objects, stats = zooniverse.parse_data(post_body)
    if not isinstance(collected_objects, dict):
        raise Exception('could not parse body')

    stats['updated'] = {}
    stats['new'] = {}
    stats['times'] = {
        'start': str(start_all),
        'parse_data': time_diff('start_parse_data'),
    }
    stats['count']['new'] = {}
    stats['count']['new_total'] = 0
    stats['count']['updated'] = {}
    stats['count']['updated_total'] = 0
    stats['events'] = []

    if collected_objects == {}:
        warn('no zooniverse data was parsed from csv')
        return stats

    unique_linked_object_values = {}

    time_now('start_apply_mapping')
    for objecttype in mappings:
        ot_mapping = mappings[objecttype]
        match_column = get_json_value(ot_mapping, 'match_column')
        if not isinstance(match_column, str):
            continue

        count_updated = 0

        # load objects with the collected signatures
        affected_objects = __load_objects_by_signature(
            objecttype,
            match_column,
            list(collected_objects.keys()),
            api_url,
            token,
        )

        event = {
            'type': 'ZOONIVERSE_IMPORT_UPDATE',
            'info_json': {'objecttype': objecttype, 'objects': []},
        }

        top_level_field_user, field_user, _ = mapping.path_from_mapping(
            mapping=ot_mapping,
            column_name='update_column_user_name',
        )
        (
            top_level_field_created,
            field_created,
            field_created_type,
        ) = mapping.path_from_mapping(
            mapping=ot_mapping,
            column_name='update_column_created_at',
        )

        # iterate over objects, update objects with mapped data
        updated_objects = []
        for signatur in affected_objects:
            if signatur not in collected_objects:
                debug(
                    'signatur {0} not in collected objects -> skip'.format(signatur),
                )
                continue

            obj = affected_objects[signatur]
            zooniverse_data = collected_objects[signatur]
            obj_changed = False

            for user_name in zooniverse_data:
                for created_at in zooniverse_data[user_name]:
                    # if the fields for the user and the date of the zooniverse entry is grouped in the same nested table,
                    # check if this pair of user and date is already in the current object.
                    # if this is the case, skip this entry completly
                    skip_zooniverse_entry = False
                    in_same_nested = False
                    if top_level_field_user == top_level_field_created:
                        in_same_nested = True
                        nested = get_json_value(obj, top_level_field_user)
                        if isinstance(nested, list):
                            for entry in nested:
                                entry_user_name = get_json_value(entry, field_user)
                                if entry_user_name != user_name:
                                    continue

                                if field_created_type in ['date', 'datetime']:
                                    entry_created_at = get_json_value(
                                        entry, '{}.value'.format(field_created)
                                    )
                                else:
                                    entry_created_at = get_json_value(
                                        entry, field_created
                                    )
                                if entry_created_at != created_at:
                                    continue

                                skip_zooniverse_entry = True
                                break

                    if skip_zooniverse_entry:
                        debug(
                            'pair of user_name="{}" and created_at="{}" is already in object with signature {} -> skip'.format(
                                user_name, created_at, signatur
                            ),
                        )
                        continue

                    debug(
                        'add zooniverse data for user_name="{}" and created_at="{}" to object with signature {}'.format(
                            user_name, created_at, signatur
                        ),
                    )

                    # decide if user and created_at are in the same nested table
                    # if so, group them together
                    if not in_same_nested:
                        # user and created_at are in different nested tables / fields, so use the default mapping.
                        # grouping will not necessarily work
                        mapping.apply(
                            obj=obj,
                            unique_linked_object_values=unique_linked_object_values,
                            mapping=ot_mapping,
                            column_name='update_column_user_name',
                            value=user_name,
                            signatur=signatur,
                            languages=languages,
                        )
                        mapping.apply(
                            obj=obj,
                            unique_linked_object_values=unique_linked_object_values,
                            mapping=ot_mapping,
                            column_name='update_column_created_at',
                            value=created_at,
                            signatur=signatur,
                            languages=languages,
                        )

                        # apply mapping to update columns
                        for k in zooniverse_data[user_name][created_at]:
                            mapping.apply(
                                obj=obj,
                                unique_linked_object_values=unique_linked_object_values,
                                mapping=ot_mapping,
                                column_name='update_column_{}'.format(k.lower()),
                                value=zooniverse_data[user_name][created_at][k],
                                signatur=signatur,
                                languages=languages,
                            )

                    else:
                        # user and created_at are in the same row of a nested table, so group them together
                        dummy_obj = {}

                        if field_created_type in ['date', 'datetime']:
                            entry_created_at = {'value': created_at}
                        else:
                            entry_created_at = created_at

                        dummy_obj[top_level_field_user] = [
                            {
                                field_user: user_name,
                                field_created: entry_created_at,
                            }
                        ]

                        for k in zooniverse_data[user_name][created_at]:
                            mapping.apply(
                                obj=dummy_obj,
                                unique_linked_object_values=unique_linked_object_values,
                                mapping=ot_mapping,
                                column_name='update_column_{}'.format(k.lower()),
                                value=zooniverse_data[user_name][created_at][k],
                                signatur=signatur,
                                languages=languages,
                                group_into_nested=top_level_field_user,
                            )

                        if top_level_field_user in obj and isinstance(
                            obj[top_level_field_user], list
                        ):
                            for entry in dummy_obj[top_level_field_user]:
                                obj[top_level_field_user].append(entry)
                        else:
                            obj[top_level_field_user] = dummy_obj[top_level_field_user]

                        debug(
                            'add zooniverse data for user_name="{}" and created_at="{}" to object with signature {}: dummy: {}'.format(
                                user_name, created_at, signatur, dumpjs(dummy_obj)
                            ),
                        )
                        debug(
                            'add zooniverse data for user_name="{}" and created_at="{}" to object with signature {}: ==>: {}'.format(
                                user_name,
                                created_at,
                                signatur,
                                dumpjs(obj[top_level_field_user]),
                            ),
                        )

                    obj_changed = True

            if not obj_changed:
                continue

            id = get_json_value(obj, '_id')
            if not isinstance(id, int):
                continue

            version = get_json_value(obj, '_version')
            if not isinstance(version, int):
                continue
            obj['_version'] = version + 1

            updated_objects.append(__build_object(objecttype, obj, languages=languages))
            count_updated += 1

            event['info_json']['objects'].append(
                {
                    '_id': id,
                    '_version': version + 1,
                    'match_column': match_column,
                    'unique_value': signatur,
                }
            )

        if len(updated_objects) < 1:
            continue

        stats['updated'][objecttype] = updated_objects
        stats['count']['updated'][objecttype] = count_updated
        stats['count']['updated_total'] += count_updated

        event['info_json']['objects'] = sorted(
            event['info_json']['objects'], key=lambda o: o['_id']
        )
        stats['events'].append(event)

    stats['times']['apply_mapping'] = time_diff('start_apply_mapping')

    time_now('start_create_new_linked_objects')
    new_linked_objects, count_new = __create_new_linked_objects(
        unique_linked_object_values,
        languages,
        api_url,
        token,
    )
    stats['new'] = new_linked_objects
    stats['count']['new'] = count_new
    stats['times']['create_new_linked_objects'] = time_diff(
        'start_create_new_linked_objects'
    )

    for objecttype in unique_linked_object_values:
        event = {
            'type': 'ZOONIVERSE_IMPORT_INSERT',
            'info_json': {
                'objecttype': objecttype,
                'unique_column': match_column,
                'unique_values': [],
            },
        }

        for unique_column in unique_linked_object_values[objecttype]:
            for v in sorted(unique_linked_object_values[objecttype][unique_column]):
                event['info_json']['unique_values'].append(v)

        stats['events'].append(event)

    count_new_total = 0
    for ot in count_new:
        count_new_total += count_new[ot]
    stats['count']['new_total'] = count_new_total

    stats['times']['total'] = time_diff('start_all')

    debug('overview: count: {0}'.format(dumpjs(stats['count'])))
    debug('overview: times: {0}'.format(dumpjs(stats['times'])))

    return stats
