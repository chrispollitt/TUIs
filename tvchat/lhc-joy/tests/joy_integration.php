<?php
/**
 * Runs the Joy extension (bootstrap.php) against stand-ins for the few Live Helper Chat classes it touches, to check the glue:
 * who she answers, what she saves, that she closes and blocks as she should, that a kill file silences her and that no error
 * escapes into the chat.  (The real site is the final test; this catches the mistakes before it.)  Usage: php tests/joy_integration.php
 */
class erLhcoreClassChatEventDispatcher
{
    public static $inst;
    public $l = array();
    public static function getInstance() { return self::$inst ?: (self::$inst = new self()); }
    public function listen($e, $cb) { $this->l[$e][] = $cb; }
    public function dispatch($e, $p) { foreach (isset($this->l[$e]) ? $this->l[$e] : array() as $cb) { call_user_func($cb, $p); } }
}
class erLhcoreClassModelChat
{
    const STATUS_PENDING_CHAT = 0, STATUS_ACTIVE_CHAT = 1, STATUS_CLOSED_CHAT = 2, STATUS_OPERATORS_CHAT = 4, STATUS_BOT_CHAT = 5;
    const STATUS_SUB_OWNER_CHANGED = 1;
    public $id = 1, $dep_id = 2, $status = 0, $status_sub = 0, $user_id = 0, $ip = '203.0.113.9', $wait_time = 0, $pnd_time = 0, $time = 0;
    public $chat_variables = '', $last_msg_id = 0, $last_op_msg_time = 0, $has_unread_op_messages = 0, $unread_op_messages_informed = 1;
    public $updates = array();
    public function __get($n) { return $n === 'chat_variables_array' ? (json_decode($this->chat_variables, true) ?: array()) : null; }
    public function updateThis($a = array()) { $this->updates[] = $a; }
}
class erLhcoreClassModelmsg
{
    const STATUS_SENT = 0;
    public static $all = array();
    public static $boom = false;
    public $id, $msg, $chat_id, $user_id, $time, $name_support, $del_st;
    public static function getList($p)
    {
        if (self::$boom) { throw new Exception('the database fell over'); }
        $out = array();
        foreach (self::$all as $m) {
            if ($m->chat_id == $p['filter']['chat_id'] && $m->user_id == $p['filter']['user_id'] && $m->id > $p['filtergt']['id']) { $out[] = $m; }
        }
        return array_slice($out, 0, $p['limit']);
    }
}
class FakeSession { public static $n = 0; public function save($m) { $m->id = ++self::$n; erLhcoreClassModelmsg::$all[] = $m; } }
class erLhcoreClassChat { public static function getSession() { return new FakeSession(); } }
class erLhcoreClassModelUser { public $id; public static function fetch($id) { $u = new self(); $u->id = $id; return $u; } }
class erLhcoreClassChatHelper
{
    public static $closed = array();
    public static function changeStatus($p) { $p['chat']->status = $p['status']; self::$closed[] = $p['chat']->id; }
}
class erLhcoreClassModelChatBlockedUser
{
    const BLOCK_IP = 0;
    public static $blocks = array();
    public static function createBlockRecord($p) { self::$blocks[] = array('ip' => $p['chat']->ip, 'expires' => $p['expires'], 'btype' => $p['btype']); }
}

// ---- the extension, copied where it can have its own settings ----
$tmp = sys_get_temp_dir() . '/joytest' . getmypid();
@mkdir($tmp . '/extension', 0777, true);
function copydir($a, $b) { @mkdir($b, 0777, true); foreach (scandir($a) as $f) { if ($f[0] === '.') continue; is_dir("$a/$f") ? copydir("$a/$f", "$b/$f") : copy("$a/$f", "$b/$f"); } }
copydir(__DIR__ . '/../extension/joy', $tmp . '/extension/joy');
copy(__DIR__ . '/../../configure/faq.example.txt', $tmp . '/faq.txt');
$cfg = array('enabled' => true, 'dep_id' => 2, 'operator_id' => 2, 'operator_name' => 'Joy, Universal Laughter assistant',
    'faq' => "$tmp/faq.txt", 'log' => "$tmp/q.log", 'abuse_words' => '', 'max_misses' => 5, 'ban_minutes' => 60, 'kill_file' => "$tmp/DISABLED",
    'alert_to' => 'boss@example.test', 'alert_from' => 'joy@example.test', 'alert_state' => "$tmp/alerts.json",
    'alert_words' => array('urgent', 'emergency', 'refund'), 'admin_url' => 'https://chat.example.test/admin/single/');
file_put_contents($tmp . '/extension/joy/settings/settings.ini.php', "<?php\nreturn " . var_export($cfg, true) . ";\n");
require $tmp . '/extension/joy/bootstrap/bootstrap.php';

$fails = 0;
function check($ok, $what) { global $fails; echo ($ok ? 'ok   ' : 'FAIL ') . $what . "\n"; if (!$ok) { $fails++; } }
function newChat($id, $over = array()) { $c = new erLhcoreClassModelChat(); $c->id = $id; $c->time = time(); foreach ($over as $k => $v) { $c->$k = $v; } return $c; }
function visitor($chat, $text, $age = 0) { $m = new erLhcoreClassModelmsg(); $m->id = ++FakeSession::$n; $m->msg = $text; $m->chat_id = $chat->id; $m->user_id = 0; $m->time = time() - $age; erLhcoreClassModelmsg::$all[] = $m; return $m; }
function sent($chat) { $o = array(); foreach (erLhcoreClassModelmsg::$all as $m) { if ($m->chat_id == $chat->id && $m->user_id == 2) { $o[] = $m->msg; } } return $o; }
function fire($event, $chat) { erLhcoreClassChatEventDispatcher::getInstance()->dispatch($event, array('chat' => &$chat)); }

$ext = new erLhcoreClassExtensionJoy();
$ext->run();
$mails = array();
JoyAlert::$mailer = function ($to, $subject, $body, $headers, $from) { global $mails; $mails[] = array($to, $subject, $body, $headers); return true; };

// 1. a chat starts with a question: a greeting and the answer, the chat is hers, the state is saved
$c1 = newChat(1);
visitor($c1, 'What should I wear?');
fire('chat.chat_started', $c1);
$s = sent($c1);
check(count($s) === 2 && strpos($s[0], 'Hello, and welcome') === 0 && strpos($s[1], 'comfortable clothing') !== false, 'a first question gets a greeting and the answer');
check($c1->status === erLhcoreClassModelChat::STATUS_ACTIVE_CHAT && $c1->user_id === 2, 'the chat becomes hers (active, her operator)');
check($c1->wait_time >= 1 && $c1->has_unread_op_messages === 1 && $c1->last_msg_id === FakeSession::$n, 'the chat record is brought up to date');
check(json_decode($c1->chat_variables, true)['joy']['greeted'] === true, 'her state is kept in the chat variables');
$n = count($s);

// 2. the same event again: nothing is answered twice; a new line is answered, without a second greeting
fire('chat.addmsguser', $c1);
check(count(sent($c1)) === $n, 'a line is never answered twice');
visitor($c1, 'how much does it cost');
fire('chat.addmsguser', $c1);
$s = sent($c1);
check(count($s) === $n + 1 && strpos($s[$n], '$100 per hour') !== false, 'the next line is answered (no second greeting)');
$log = file("$tmp/q.log", FILE_IGNORE_NEW_LINES);
$rec = json_decode($log[1], true);
check(count($log) === 2 && $rec['r'] === 'answer' && $rec['chat'] === 'lhc_pro/1' && $rec['e'] !== '', 'the questions log has a line for each');

// 3. not her chats
$c2 = newChat(2, array('dep_id' => 1));
visitor($c2, 'What should I wear?');
fire('chat.addmsguser', $c2);
check(sent($c2) === array(), 'another department is left alone');
$c3 = newChat(3, array('status' => 1, 'user_id' => 5));
visitor($c3, 'What should I wear?');
fire('chat.addmsguser', $c3);
check(sent($c3) === array(), 'a chat a person has taken is left alone');
$c4 = newChat(4, array('status' => erLhcoreClassModelChat::STATUS_CLOSED_CHAT));
visitor($c4, 'What should I wear?');
fire('chat.addmsguser', $c4);
check(sent($c4) === array(), 'a closed chat is left alone');

// 4. abuse: a flag, then the end: closed, and the address is turned away
$c5 = newChat(5);
visitor($c5, 'you stupid f*cking bot');
fire('chat.addmsguser', $c5);
check(count(sent($c5)) === 1 && strpos(sent($c5)[0], 'respectful') !== false && $c5->status !== erLhcoreClassModelChat::STATUS_CLOSED_CHAT, 'abuse is flagged politely once');
visitor($c5, 'this is shit');
fire('chat.addmsguser', $c5);
check(count(sent($c5)) === 2 && strpos(sent($c5)[1], 'ending this chat') !== false && $c5->status === erLhcoreClassModelChat::STATUS_CLOSED_CHAT, 'the second time she says goodbye and the chat is closed');
check(count(erLhcoreClassModelChatBlockedUser::$blocks) === 1 && erLhcoreClassModelChatBlockedUser::$blocks[0]['ip'] === '203.0.113.9'
    && erLhcoreClassModelChatBlockedUser::$blocks[0]['expires'] > time() + 3000, 'the address is turned away for an hour');

// 5. lost: six misses in a row end the chat, with no ban
$blocks = count(erLhcoreClassModelChatBlockedUser::$blocks);
$c6 = newChat(6);
foreach (array('zork', 'blarg', 'quux', 'frobnicate', 'xyzzy', 'plugh') as $w) { visitor($c6, $w); fire('chat.addmsguser', $c6); }
$s = sent($c6);
check(strpos(end($s), 'You seem to be having an issue') === 0 && $c6->status === erLhcoreClassModelChat::STATUS_CLOSED_CHAT, 'the sixth miss in a row ends the chat');
check(count(erLhcoreClassModelChatBlockedUser::$blocks) === $blocks, 'a lost visitor is not blocked');

// 6. a chat from before the extension: its old line is not answered, a new one is
$c7 = newChat(7);
visitor($c7, 'What should I wear?', 4000);
fire('chat.addmsguser', $c7);
check(sent($c7) === array(), 'an old line in a chat from before is not answered');
visitor($c7, 'how much does it cost');
fire('chat.addmsguser', $c7);
check(count(sent($c7)) >= 1, 'a new one is');

// 7. the kill file silences her; an error never reaches the chat
touch($cfg['kill_file']);
$c8 = newChat(8);
visitor($c8, 'What should I wear?');
fire('chat.addmsguser', $c8);
check(sent($c8) === array(), 'the kill file silences her');
unlink($cfg['kill_file']);
erLhcoreClassModelmsg::$boom = true;
$c9 = newChat(9);
$threw = false;
try { fire('chat.addmsguser', $c9); } catch (Throwable $e) { $threw = true; }
check(!$threw, 'an error inside is caught and does not reach the chat');
erLhcoreClassModelmsg::$boom = false;
check(count($mails) === 1 && $mails[0][0] === 'boss@example.test' && $mails[0][1] === 'Joy is failing' && strpos($mails[0][2], 'the database fell over') !== false
    && strpos($mails[0][3], 'From: Joy <joy@example.test>') === 0, 'her own failure is mailed at once');
erLhcoreClassModelmsg::$boom = true;
fire('chat.addmsguser', newChat(10));
erLhcoreClassModelmsg::$boom = false;
check(count($mails) === 1, 'the same failure is not mailed again within the hour');

// 8. a visitor with an alert word: she answers as usual AND a person is told, once per chat, with the chat's link
$mails = array();
$c11 = newChat(11);
visitor($c11, 'This is URGENT, my refund has not come, call me on 604 555 0100');
fire('chat.addmsguser', $c11);
check(count(sent($c11)) >= 1, 'she still answers a visitor who used an alert word');
check(count($mails) === 1 && strpos($mails[0][1], '"urgent"') !== false && strpos($mails[0][2], 'https://chat.example.test/admin/single/11') !== false
    && strpos($mails[0][2], '604 555 0100') === false, 'a person is told at once, with the link, and the number is blanked');
visitor($c11, 'it is an emergency');
fire('chat.addmsguser', $c11);
check(count($mails) === 1, 'the same chat is not mailed twice in an hour');
$c12 = newChat(12);
visitor($c12, 'What should I wear? (not urgently)');
fire('chat.addmsguser', $c12);
check(count($mails) === 1, 'a word that merely starts with an alert word is no alert');
$c13 = newChat(13);
visitor($c13, 'Where is my refund?');
fire('chat.addmsguser', $c13);
check(count($mails) === 2, 'another chat is another alert');

// 9. the throttle: ten mails an hour in all; alerts off when alert_to is empty
$mails = array();
for ($i = 20; $i < 40; $i++) { $c = newChat($i); visitor($c, 'refund'); fire('chat.addmsguser', $c); }
check(count($mails) === 7, 'ten mails an hour at most (three had gone already), however many chats');
check(JoyAlert::send(array('alert_to' => '', 'alert_state' => ''), 'x', 's', 'b') === false, 'with no alert_to nothing is sent');
$st = "$tmp/s.json";
check(JoyAlert::due($st, 'k', 1000) && !JoyAlert::due($st, 'k', 1500) && JoyAlert::due($st, 'k', 5000), 'due: once, then not for an hour, then again');

echo $fails ? "$fails FAILED\n" : "all passed\n";
exit($fails ? 1 : 0);
