# fylr-plugin-zooniverse-import

A [fylr](https://docs.fylr.io) plugin that imports CSV exports from [Zooniverse](https://www.zooniverse.org) into existing records.

Zooniverse exports one row per classification. Each row holds the answers of one volunteer for one subject. The plugin reads such a file, matches every row against a record in fylr and writes the answers into the fields you configured. Values that belong to a linked object type are created as new linked records if they do not exist yet.

## Usage

The import is opened from the plugin menu. Users need the system right *Use Zooniverse Import*.

1. Select the CSV file with the Zooniverse export.
2. Start the parsing. The plugin reads the file in batches and shows how many rows and records were found.
3. Check the overview. It lists the records to update and the linked records to create.
4. Start the import.

Nothing is written before step 4. Every import writes the events `ZOONIVERSE_IMPORT_UPDATE` and `ZOONIVERSE_IMPORT_INSERT`, so the result can be checked in the event log.

## Configuration

The mappings are configured in the base configuration under *Zooniverse Import*. Each row of the table maps one object type to the columns of the Zooniverse export. Several object types can be mapped at the same time.

| Setting | Meaning |
| --- | --- |
| *Mapping enabled* | Only enabled rows are used during an import. |
| *Objecttype* | The object type whose records are updated. |
| *Field for match* | The field that is compared with the Zooniverse signature. The signature is the file name in the CSV column `subject_data` without its extension. The value must be unique. |
| *Target field T0* to *Target field T4* | The fields that receive the answers of the tasks `T0` to `T4` from the CSV column `annotations`. |
| *Split values* | Splits an answer at `,` and `;` into several values. This only works for fields in a nested table. |
| *Target field created_at* | The field that receives the date from the CSV column `created_at`. |
| *Target field user_name* | The field that receives the volunteer name from the CSV column `user_name`. |

Records are matched by the field configured as *Field for match*. Rows without a match are skipped.

A target field can sit on the top level of the record, in a nested table or in a linked object type. Put *Target field created_at* and *Target field user_name* into the same nested table to keep the answers of one classification together. Each classification then becomes one row of that table. A classification that is already in the record is recognized by the pair of name and date, so an import can be repeated without creating duplicates.

The languages of the written text fields come from the database languages of the fylr instance.
