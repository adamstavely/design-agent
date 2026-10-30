# Surfaces: Slack, the API and the CLI

Every surface reaches the same agent. What differs is presentation: in Slack the agent shows its
work as it goes, takes follow-ups mid-job, and ends each reply with a short footer.

## Slack presentation

Code: `src/design_agent/surfaces/slack/` (Web API client, who a run answers, step phrases, the
stream) and `src/design_agent/middleware/{progress,footer,steering}.py`.

- **Live progress.** One message per turn shows a card per step in plain studio language
  ("Studying the brand", "Rendering the teaser"), through Slack's streaming API
  (`chat.startStream`/`appendStream`/`stopStream`), or one message redrawn with `chat.update` when
  streaming is refused. It settles to "Done", or is deleted when the turn's reply is `[quiet]`.
  Cards never carry argument text: no paths, queries, file ids or anybody's words. Each background
  helper streams its own message.
- **Footer.** Only what changes: agents still running, render progress, time so far and the
  estimated cost of the job (`2 agents running • render 45% • 12 min • ~$12.40 so far`), metered
  locally.
- **Steering.** A follow-up while helpers run starts a turn at once (the lead ends its turn after
  handing work off), sees what is running, and can pass the note to a helper with `steer_task`;
  the helper reads it before its next model call. While the lead's own turn is busy, it reads new
  messages in the thread before each model call and folds them in; "stop", "cancel" or "never mind"
  ends the turn at its next step.

The bot token stays on the host: progress and footer call the Slack Web API from middleware, and
the sandbox never sees a token, a Slack URL or a Slack payload.

## Self-hosted Slack

`design-agent slack` runs a Bolt app in one process with the agent (Socket Mode by default, or
signed HTTP on `/slack/events`): mentions and direct messages start turns (every message in the
app's channels with `DESIGN_AGENT_SLACK_TRIGGER_ON_ALL_MESSAGES`), duplicate deliveries start one,
attachments are fetched on the host with the bot token, finals upload to the thread (with a copy in
`workspace/out/<thread>/`), and wake-up replies post there. Turns on one thread queue behind each
other; a follow-up that arrives while a turn runs is folded into it by the steering middleware. The
same middleware draws progress and the footer. Manifest: `config/slack-manifest.example.yaml`.
Setup: [self-hosting](../self-hosting.md).

## The API and the CLI

The API returns each turn's reply and delivered files, and background news through
`GET /threads/{id}/updates` ([self-hosting](../self-hosting.md)). The CLI prints each step's tool
name as it runs and stays open while background work continues.
