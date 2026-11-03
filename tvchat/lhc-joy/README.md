# lhc-joy: Joy as a Live Helper Chat extension

Joy is the Universal Laughter assistant: she answers visitors from a FAQ file. Until now she ran inside Floyd's daemon on cpbee
(`faq = FILE` on a `protocol = floyd` account), reaching the chat site over the internet and polling it. This is the same Joy as a
**Live Helper Chat extension that runs on the web host itself**: when a visitor writes in her department she answers inside the
site's own request, so there is no daemon, no polling and nothing on cpbee in the loop.

```
visitor writes ──> Live Helper Chat saves the line ──> event chat.addmsguser / chat.chat_started
                                                           │
                              extension/joy/bootstrap/bootstrap.php   (erLhcoreClassExtensionJoy)
                                                           │  JoyBrain::reply()   (the FAQ matching, typos, commands, abuse ...)
                                                           ▼
                           her line(s) saved as operator 2's, the chat made hers; the visitor's widget shows them at its next look
```

## Layout

| | |
|---|---|
| `extension/joy/bootstrap/bootstrap.php` | the glue: listens for the two events, saves her lines like the REST call `addmsgadmin`, closes and blocks, logs |
| `extension/joy/classes/JoyBrain.php` | her brain, a PHP port of `faq_*` and `FloydAdapter._faq_hear` in `backend/tvchat-backend`; knows nothing of Live Helper Chat |
| `extension/joy/classes/JoyJokes.php` | **generated** by `build/gen_joy_jokes` (`build/gen_jokes.py`) from `FloydMind.JOKES`; do not edit |
| `extension/joy/settings/settings.example.php` | her settings (department, operator, paths, limits); the deploy writes `settings.ini.php` from it |
| `tests/gen_joy_golden.py` | drives the real Python Floyd-pro through ~100 steps of conversation and writes `tests/golden.json` |
| `tests/joy_golden.php` | holds the PHP brain to that, line for line (**the drift guard**) |
| `tests/joy_integration.php` | the extension against stand-ins for the few Live Helper Chat classes it uses |

Her FAQ and log live **outside** the web root, in `/home/whatwelo/joy/` (`faq.txt`, `questions.log`, the kill file `DISABLED`).

## Keeping the PHP and the Python from drifting

The Python Floyd-pro stays in the repo as the **reference** (and the way back). When Joy's behaviour changes:

1. change `backend/tvchat-backend` (and its tests) first, then port the change to `JoyBrain.php`;
2. `python3 lhc-joy/tests/gen_joy_golden.py` (about 5 minutes) and `php lhc-joy/tests/joy_golden.php`: no mismatch;
3. `php lhc-joy/tests/joy_integration.php`: all passed;
4. deploy with `deploy/joy-webhost-on-whatwelove.sh`.

Known, deliberate differences: the reference sends each LINE of an answer as its own chat bubble, the PHP one sends the answer as one bubble
with line breaks; the reference has a cap of lines per chat per hour (the extension has none); the reference pauses before answering,
the extension answers at once; `!joke` is random in both (the test checks what a joke is, not which).

## Switching it on and off (never both at once: a visitor would get two answers)

```
deploy/joy-webhost-on-whatwelove.sh              ACTION=install   put it on the host, SILENT
deploy/joy-cpbee-pro.sh                          ACTION=off       take the old Joy out of Floyd's config on cpbee
deploy/joy-webhost-on-whatwelove.sh              ACTION=enable    she answers

deploy/joy-webhost-on-whatwelove.sh              ACTION=disable   the way back ...
deploy/joy-cpbee-pro.sh                          ACTION=on        ... and the old Joy returns
```

`ACTION=status` on the host script says what is installed. Every error inside the extension is caught and written to the PHP error
log as `[joy] ...`; it can never break the chat.
