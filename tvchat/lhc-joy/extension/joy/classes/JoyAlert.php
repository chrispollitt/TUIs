<?php
/**
 * Joy's urgent mail to the person who looks after her: sent AT ONCE (Floyd's daily check-in is for the slow things).
 * What is urgent: her own code failing, and a visitor using one of the alert words (settings 'alert_words').  She still answers
 * the visitor as usual; this only tells a person.  Throttled (the same thing once an hour; ten mails an hour in all) so a bug or a
 * flood can never become a flood of mail.  Nothing here may throw into the chat.
 */
class JoyAlert
{
    /** For tests: function ($to, $subject, $body, $headers, $from) returning bool, instead of mail(). */
    public static $mailer = null;

    /** The alert word the text uses (whole word, any case), or null. */
    public static function urgent($text, array $words)
    {
        foreach ($words as $w) {
            $w = trim((string)$w);
            if ($w !== '' && preg_match('/(?<![\p{L}\p{N}])' . preg_quote($w, '/') . '(?![\p{L}\p{N}])/iu', $text)) {
                return $w;
            }
        }
        return null;
    }

    /** May this alert go out now?  Records it if so.  $key: what it is about (the same key waits $gap seconds). */
    public static function due($stateFile, $key, $now = null, $gap = 3600, $cap = 10)
    {
        $now = $now === null ? time() : $now;
        $st = array();
        if ($stateFile !== '' && is_file($stateFile)) {
            $d = json_decode((string)@file_get_contents($stateFile), true);
            $st = is_array($d) ? $d : array();
        }
        $keys = (isset($st['keys']) && is_array($st['keys'])) ? $st['keys'] : array();
        $all = array();
        foreach ((isset($st['all']) && is_array($st['all']) ? $st['all'] : array()) as $t) {
            if ($now - $t < 3600) { $all[] = $t; }
        }
        if (isset($keys[$key]) && $now - $keys[$key] < $gap) { return false; }
        if (count($all) >= $cap) { return false; }
        foreach ($keys as $k => $t) {
            if ($now - $t > 86400) { unset($keys[$k]); }
        }
        $keys[$key] = $now;
        $all[] = $now;
        if ($stateFile !== '') {
            @mkdir(dirname($stateFile), 0700, true);
            @file_put_contents($stateFile, json_encode(array('keys' => $keys, 'all' => $all)), LOCK_EX);
        }
        return true;
    }

    /** Mail it if alerts are on and it is due.  Returns true when a mail was handed over.  Never throws. */
    public static function send(array $cfg, $key, $subject, $body)
    {
        try {
            $to = isset($cfg['alert_to']) ? trim($cfg['alert_to']) : '';
            if ($to === '' || !self::due(isset($cfg['alert_state']) ? $cfg['alert_state'] : '', $key)) {
                return false;
            }
            $from = !empty($cfg['alert_from']) ? $cfg['alert_from'] : 'joy@universal-laughter.com';
            $subject = preg_match('/[^\x20-\x7e]/', $subject) ? '=?UTF-8?B?' . base64_encode($subject) . '?=' : $subject;
            $headers = "From: Joy <$from>\r\nContent-Type: text/plain; charset=UTF-8\r\nAuto-Submitted: auto-generated\r\nX-Joy-Alert: $key";
            $body = $body . "\n\n(Joy's urgent alert. Floyd's daily check-in covers the slow things.)\n";
            if (self::$mailer) {
                return (bool)call_user_func(self::$mailer, $to, $subject, $body, $headers, $from);
            }
            return (bool)@mail($to, $subject, $body, $headers, '-f' . $from);
        } catch (\Throwable $e) {
            error_log('[joy] the alert could not be sent: ' . $e->getMessage());
            return false;
        }
    }
}
