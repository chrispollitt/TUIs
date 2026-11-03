<?php
/**
 * Joy, the Universal Laughter assistant, as a Live Helper Chat extension: when a visitor writes in her department she
 * answers AT ONCE from the FAQ file (JoyBrain), inside the visitor's own request: no daemon, no polling, nothing on cpbee.
 * Enabled by 'joy' in the 'extensions' list of settings/settings.ini.php; configured by extension/joy/settings/settings.ini.php;
 * switched off by creating the kill_file.  Every error is caught and logged: a bug here must never break the chat.
 */
#[\AllowDynamicProperties]
class erLhcoreClassExtensionJoy
{
    private $cfg = null;
    private $faq = null;

    public function __construct()
    {
    }

    public function run()
    {
        $base = dirname(__DIR__) . '/classes/';
        require_once $base . 'JoyBrain.php';
        require_once $base . 'JoyJokes.php';
        require_once $base . 'JoyAlert.php';
        $dispatcher = erLhcoreClassChatEventDispatcher::getInstance();
        $dispatcher->listen('chat.addmsguser', array($this, 'onVisitor'));       // a visitor's later lines
        $dispatcher->listen('chat.chat_started', array($this, 'onVisitor'));     // the first line, as the chat starts
    }

    public function onVisitor($params)
    {
        try {
            $this->process($params['chat']);
        } catch (\Throwable $e) {
            $what = $e->getMessage() . ' at ' . $e->getFile() . ':' . $e->getLine();
            error_log('[joy] ' . $what);
            try {
                $cfg = $this->config();
                JoyAlert::send($cfg, 'error', 'Joy is failing', "Joy's code threw an error while answering a visitor (the chat itself is unharmed, but she may not be answering):\n\n" . $what
                    . "\n\nIf it keeps happening, silence her with: touch " . (isset($cfg['kill_file']) ? $cfg['kill_file'] : 'her kill file') . "\n");
            } catch (\Throwable $e2) {
                error_log('[joy] and the alert failed too: ' . $e2->getMessage());
            }
        }
    }

    /** Tell a person at once when a visitor uses an alert word; she answers as usual. */
    private function alertWords($cfg, $chat, $text)
    {
        $w = JoyAlert::urgent($text, isset($cfg['alert_words']) && is_array($cfg['alert_words']) ? $cfg['alert_words'] : array());
        if ($w === null) {
            return;
        }
        $link = rtrim(isset($cfg['admin_url']) ? $cfg['admin_url'] : 'https://chat.universal-laughter.com/index.php/site_admin/chat/single/', '/') . '/' . $chat->id;
        JoyAlert::send($cfg, 'urgent/' . $chat->id, 'A visitor wrote "' . $w . '" to Joy (chat ' . $chat->id . ')',
            "A visitor in Joy's chat used the word \"$w\":\n\n  " . mb_substr(JoyBrain::redact($text), 0, 300) . "\n\nJoy has answered from her FAQ as usual; this may need a person.\nThe chat: $link\n");
    }

    private function config()
    {
        if ($this->cfg === null) {
            $file = dirname(__DIR__) . '/settings/settings.ini.php';
            $c = is_file($file) ? include $file : null;
            $this->cfg = is_array($c) ? $c : array();
        }
        return $this->cfg;
    }

    private function faq($cfg)
    {
        if ($this->faq === null) {
            $this->faq = JoyBrain::parseFaq((string)@file_get_contents($cfg['faq']));
        }
        return $this->faq;
    }

    private function process($chat)
    {
        $cfg = $this->config();
        if (empty($cfg['enabled']) || (!empty($cfg['kill_file']) && is_file($cfg['kill_file']))) {
            return;
        }
        if ((int)$chat->dep_id !== (int)$cfg['dep_id']) {
            return;                                                             // not Joy's department
        }
        if (!in_array($chat->status, array(erLhcoreClassModelChat::STATUS_PENDING_CHAT, erLhcoreClassModelChat::STATUS_ACTIVE_CHAT,
            erLhcoreClassModelChat::STATUS_BOT_CHAT))) {
            return;                                                             // closed, or an operators' chat
        }
        if ((int)$chat->user_id > 0 && (int)$chat->user_id !== (int)$cfg['operator_id']) {
            return;                                                             // a person has taken the chat
        }
        $vars = $chat->chat_variables_array;
        $vars = is_array($vars) ? $vars : array();
        $st = (isset($vars['joy']) && is_array($vars['joy'])) ? $vars['joy'] : array();
        $last = (int)(isset($st['last']) ? $st['last'] : 0);
        $fresh = !isset($st['last']);                                          // a chat from before this extension: its old lines stay unanswered
        $msgs = erLhcoreClassModelmsg::getList(array('filter' => array('chat_id' => $chat->id, 'user_id' => 0),
            'filtergt' => array('id' => $last), 'sort' => 'id ASC', 'limit' => 20));
        if (!$msgs) {
            return;
        }
        $faq = $this->faq($cfg);
        $opt = array('max_misses' => isset($cfg['max_misses']) ? (int)$cfg['max_misses'] : 5, 'abuse_extra' => $this->extraWords($cfg));
        $say = array();
        $end = null;
        $notes = array();
        foreach ($msgs as $m) {
            $last = max($last, (int)$m->id);
            $text = trim((string)$m->msg);
            if ($text === '' || ($fresh && (int)$m->time < time() - 120)) {
                continue;
            }
            $this->alertWords($cfg, $chat, $text);
            $r = JoyBrain::reply($faq, $st, $text, $opt);
            foreach ($r['say'] as $line) {
                $say[] = $line;
            }
            if ($r['note']) {
                $notes[] = array($text, $r['note']);
            }
            if ($r['end']) {
                $end = $r['end'];
                break;
            }
        }
        $st['last'] = $last;
        $vars['joy'] = $st;                                                     // saved FIRST: a second request will not answer the same line
        $chat->chat_variables = json_encode($vars);
        $chat->updateThis(array('update' => array('chat_variables')));
        foreach ($notes as $n) {
            $this->note($cfg, $chat, $n[0], $n[1]);
        }
        foreach ($say as $line) {
            $this->say($chat, $cfg, $line);
        }
        if ($end !== null) {
            $this->endChat($chat, $cfg, $end);
        }
    }

    private function extraWords($cfg)
    {
        $out = array();
        if (!empty($cfg['abuse_words']) && is_file($cfg['abuse_words'])) {
            foreach ((array)@file($cfg['abuse_words'], FILE_IGNORE_NEW_LINES) as $w) {
                $w = trim(strtolower($w));
                if ($w !== '' && $w[0] !== '#') {
                    $out[] = JoyBrain::squash($w);
                }
            }
        }
        return $out;
    }

    /** One operator line, as the REST call addmsgadmin would add it (so it looks like any operator's). */
    private function say($chat, $cfg, $text)
    {
        $opId = (int)$cfg['operator_id'];
        $msg = new erLhcoreClassModelmsg();
        $msg->msg = $text;
        $msg->chat_id = $chat->id;
        $msg->user_id = $opId;
        $msg->time = time();
        $msg->name_support = $cfg['operator_name'];
        $msg->del_st = erLhcoreClassModelmsg::STATUS_SENT;
        erLhcoreClassChat::getSession()->save($msg);
        $fields = array('last_msg_id', 'last_op_msg_time', 'has_unread_op_messages', 'unread_op_messages_informed');
        $chat->last_msg_id = $msg->id;
        $chat->last_op_msg_time = time();
        $chat->has_unread_op_messages = 1;
        $chat->unread_op_messages_informed = 0;
        if ($chat->status == erLhcoreClassModelChat::STATUS_PENDING_CHAT || $chat->status == erLhcoreClassModelChat::STATUS_BOT_CHAT) {
            $chat->status = erLhcoreClassModelChat::STATUS_ACTIVE_CHAT;         // she has taken the chat
            $chat->status_sub = erLhcoreClassModelChat::STATUS_SUB_OWNER_CHANGED;
            $chat->user_id = $opId;
            if ((int)$chat->wait_time === 0) {
                $chat->wait_time = max(1, time() - ((int)$chat->pnd_time > 0 ? (int)$chat->pnd_time : (int)$chat->time));
            }
            array_push($fields, 'status', 'status_sub', 'user_id', 'wait_time');
        }
        $chat->updateThis(array('update' => $fields));
    }

    /** Close the chat as Joy; for abuse also turn the address away for ban_minutes (a lost visitor is not an abuser). */
    private function endChat($chat, $cfg, $why)
    {
        if ($why === 'abuse' && !empty($chat->ip) && (int)(isset($cfg['ban_minutes']) ? $cfg['ban_minutes'] : 0) > 0) {
            erLhcoreClassModelChatBlockedUser::createBlockRecord(array('chat' => $chat, 'btype' => erLhcoreClassModelChatBlockedUser::BLOCK_IP,
                'expires' => time() + 60 * (int)$cfg['ban_minutes']));
        }
        $user = erLhcoreClassModelUser::fetch((int)$cfg['operator_id']);
        if ($user instanceof erLhcoreClassModelUser) {
            erLhcoreClassChatHelper::changeStatus(array('user' => $user, 'chat' => $chat,
                'status' => erLhcoreClassModelChat::STATUS_CLOSED_CHAT, 'allow_close_remote' => true));
        }
    }

    /** One JSON line per visitor line in the questions log (the format of Floyd-pro's, so faq-report reads it). */
    private function note($cfg, $chat, $text, $note)
    {
        if (empty($cfg['log'])) {
            return;
        }
        $rec = array('t' => gmdate('Y-m-d\TH:i:s\Z'), 'chat' => 'lhc_pro/' . $chat->id, 'q' => mb_substr(JoyBrain::redact($text), 0, 300),
            'r' => $note[0], 'e' => $note[1], 's' => round($note[2], 2), 'c' => round($note[3], 2));
        $dir = dirname($cfg['log']);
        if (!is_dir($dir)) {
            @mkdir($dir, 0700, true);
        }
        if (is_file($cfg['log']) && filesize($cfg['log']) > 1000000) {
            @rename($cfg['log'], $cfg['log'] . '.1');
        }
        @file_put_contents($cfg['log'], json_encode($rec, JSON_UNESCAPED_UNICODE) . "\n", FILE_APPEND | LOCK_EX);
    }
}
