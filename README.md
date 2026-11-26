# AgentShield

AgentShield is a local chat workspace for NVIDIA Nemotron. It can answer general questions or search documents you upload and approve. Each answer has an Activity trace with the input checks, provider calls, tool authorization, retrieved sources and elapsed time.

## Run on Windows

Open [Start AgentShield.cmd](Start%20AgentShield.cmd). When the terminal reports that the server is listening, open [AgentShield](http://127.0.0.1:8765/).

The server uses `nvidia/nemotron-3.5-lightning-30b-a3b`, which supports hosted inference and function calling. [NVIDIA model card](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/build)

To set or replace the credential, run the following in PowerShell, then restart the server.

```powershell
& '.\Set NVIDIA Key.ps1'
```

The key is stored under the current Windows account using DPAPI. An existing `NVIDIA_API_KEY` process environment variable takes precedence. The app binds to loopback. Conversations and source text are saved in a SQLite database under `%LOCALAPPDATA%\AgentShield`.

## Use the workspace

1. Send a message in **AgentShield Chat** for a live NVIDIA reply. Use **Recent chats** to reopen a saved conversation.
2. Open **Knowledge library** and upload a PDF, TXT or Markdown file. AgentShield scans the file name and passages on upload. A flagged source is quarantined and its review shows the check and passage location. Approve clean sources after reviewing the extracted text. PDF upload supports selectable text and includes page references.
3. Enable **Use approved sources** before asking a document question. The model's search proposal passes through the tool allowlist and schema checks before SQLite retrieval. AgentShield rechecks source approval, the SHA-256 fingerprint and candidate passages before building model context. Activity shows which sources were selected or withheld and why.
4. Click **Inspect this run** under an answer to review its trace. **Export** downloads the selected run's activity as JSON, including source identifiers and page labels.

You can also choose **Attach document** below the chat box. Uploads are scanned locally and added to the Knowledge library for review. An approved attachment limits that message's search to that file. A quarantined, unapproved or changed attachment stops the turn before a model request.

## Show the pre-model security layer

Open **Prompt examples** below the composer and load the ordinary question. Send it, then check that Activity moves through **Pre-model security** to a real NVIDIA request. Repeat with **Prompt injection** and **Jailbreak-style input**. Each blocked run expands the local checks, highlights the match and shows zero model requests. These examples measure AgentShield's gateway behavior. A blocked message stops before Nemotron can evaluate it.

## Check the model reply

Select **Output redaction** under **Prompt examples** to ask Nemotron for a dummy email address. **Activity → Output check** shows which rules ran and how many values were replaced. The gateway masks email addresses and recognized API key patterns before saving or displaying the model reply. In Knowledge mode, it also replaces source markers that do not match a passage selected for that turn. This check validates source markers. The cited passage still needs to support the answer.

Approved passages and eligible chat messages are sent to NVIDIA when used for inference. The input checks are heuristic and may miss or overflag text. Source approval and the tool allowlist are separate backend checks.

The [team project guide](output/pdf/AgentShield_Project_Guide.pdf) explains the implementation, demo flow and viva questions in easy English.
The [document security test PDF](output/pdf/AgentShield_Document_Security_Test.pdf) contains harmless examples that trigger the scanner.
