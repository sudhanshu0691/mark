# Configuration

`llm.json` selects the inference backend and model settings. Personal configuration files are ignored by Git. Copy only the examples you need, then edit the local copies:

| Example | Local file | Purpose |
| --- | --- | --- |
| `api_keys.example.json` | `api_keys.json` | Optional legacy credential and platform settings. |
| `runtime.example.env` | `runtime.env` | Optional environment overrides, messaging, and email settings. |
| `contacts.example.json` | `contacts.json` | Private contacts. |
| `permissions.example.json` | `permissions.json` | Local file and system access policy. |
| `phone.example.json` | `phone.json` | Phone and voice settings. |

For example, in PowerShell run `Copy-Item config/runtime.example.env config/runtime.env`, then uncomment and fill in the settings you need. Do not overwrite an existing local configuration unless you intend to replace it. Credentials may also be saved through MARK's UI or the OS credential store. Never commit the local copies.

Camera configuration has its own template: copy `vision_department/config/model_config.example.yaml` to `vision_department/config/model_config.yaml` and set the device ID locally. Face enrollment images and runtime memory are local data and are not distributed as examples.

`allow_project_edits` in the local permissions file permits MARK to edit source files in its project when set to `true`; it defaults to `false` for new installations. Private configuration, environment files, Git metadata, identity images and the virtual environment remain protected. Other access rules still apply.
