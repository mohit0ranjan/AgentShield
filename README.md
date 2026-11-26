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

## Draft and review email

From normal chat, type a command such as `Email friend@example.com about Friday's workshop`. AgentShield fills in the recipient, generates a draft, and opens its security review. If the command doesn't include a single recipient address, enter one in the composer to complete the review. The recipient field is handled locally, and addresses in chat email commands are removed from the drafting instruction before it goes to NVIDIA.

You can also open **Draft email** under the chat box. Describe the message and click **Generate draft**, or enter the subject and message yourself. Enter a single recipient and click **Review email** to see the exact sender, recipient, subject, and body. AgentShield sends that reviewed version only after you click **Confirm & send**. Editing a field clears the preview, and a review expires after 10 minutes.

Draft instructions are screened before contacting NVIDIA. The email checker inspects subject and body formatting, injection patterns, configured secrets, requests for credentials, urgent payment language, threats, impersonation signals, risky links, and unfinished placeholders. The drafting model can also decline a harmful drafting request. The confirmation endpoint repeats the checks. Delivery uses authenticated SMTP with TLS. A review is used at most once, and AgentShield reports uncertain delivery without attempting a resend. The content rules are heuristic and can miss or overflag text.

Drafting and review work without a sending account. NVIDIA is required for AI-generated drafts. Manual drafts work without it.

### Connect Gmail from AgentShield

Open **Gmail setup**, enter your personal Gmail address and a [Google app password](https://myaccount.google.com/apppasswords), then click **Connect Gmail**. Google requires 2-Step Verification for app passwords and some accounts disallow them. AgentShield verifies the login over implicit TLS at `smtp.gmail.com:465` and encrypts the account details for the current Windows user with DPAPI. The setup check does not send a test email. Click **Disconnect Gmail** to remove the saved connection. See [Google's app password instructions](https://support.google.com/accounts/answer/185833).

### Use another SMTP provider

Set these environment variables in PowerShell using your provider's SMTP settings, then launch the server from the same shell. A Gmail account saved in the app takes precedence.

```powershell
$env:AGENTSHIELD_SMTP_HOST = 'smtp.example.com'
$env:AGENTSHIELD_SMTP_PORT = '587'
$env:AGENTSHIELD_SMTP_TLS = 'starttls'
$env:AGENTSHIELD_SMTP_USERNAME = 'your-address@example.com'
$env:AGENTSHIELD_SMTP_FROM = 'your-address@example.com'
$smtpSecret = Read-Host 'SMTP password' -AsSecureString
$env:AGENTSHIELD_SMTP_PASSWORD = [System.Net.NetworkCredential]::new('', $smtpSecret).Password
$smtpSecret.Dispose()
& '.\start.ps1'
```

Use `ssl` and the provider's port for implicit TLS. If omitted, the port defaults to 587 for STARTTLS or 465 for implicit TLS. The sender defaults to the SMTP username when it is an email address. Review snapshots are held in the local SQLite workspace.

## Show the pre-model security layer

Open **Prompt examples** below the composer and load the ordinary question. Send it, then check that Activity moves through **Pre-model security** to a real NVIDIA request. Repeat with **Prompt injection** and **Jailbreak-style input**. Each blocked run expands the local checks, highlights the match and shows zero model requests. These examples measure AgentShield's gateway behavior. A blocked message stops before Nemotron can evaluate it.

## Check the model reply

Select **Output redaction** under **Prompt examples** to ask Nemotron for a dummy email address. **Activity → Output check** shows which rules ran and how many values were replaced. The gateway masks email addresses and recognized API key patterns before saving or displaying the model reply. In Knowledge mode, it also replaces source markers that do not match a passage selected for that turn. This check validates source markers. The cited passage still needs to support the answer.

Approved passages and eligible chat messages are sent to NVIDIA when used for inference. The input checks are heuristic and may miss or overflag text. Source approval and the tool allowlist are separate backend checks.

The [team project guide](output/pdf/AgentShield_Project_Guide.pdf) explains the implementation, demo flow and viva questions in easy English.
The [document security test PDF](output/pdf/AgentShield_Document_Security_Test.pdf) contains harmless examples that trigger the scanner.
