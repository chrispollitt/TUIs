<?php
/**
 * Joy's brain: Floyd-pro's FAQ matching, ported from tvchat-backend (faq_stems, faq_fix, faq_rank, faq_abusive,
 * parse_faq and the strict-mode part of FloydAdapter._faq_hear).  Plain PHP: it knows nothing of Live Helper Chat, so
 * it can be tested on its own (tests/joy_golden.php checks it against the Python original, answer for answer).
 */
class JoyBrain
{
    const FAQ_MIN = 2.0;        // the least score that counts as an answer ...
    const FAQ_COVER = 0.3;      // ... and the least share of the question's words it explains

    const STOP_TEXT = "a an the and or of to in on at for from with about is are was were be been am do does did can could would
        should will may might i you we they it this that these those my your our me us what who whom how when where
        why which if so as by not no yes please there here have has had any some tell he him his she her them their";

    const HELP_TEXT = "I'm Joy, Universal Laughter's virtual assistant. There are no commands: just ask me a question in plain words, for example \"What is Laughter Yoga?\", \"What should I wear?\" or \"How much does a session cost?\" You can also say hello, say thanks, or ask for a joke.";

    private static $stop = null;
    private static $swearStems = null, $insultStems = null, $abuseWords = null;

    public static function stop()
    {
        if (self::$stop === null) {
            self::$stop = array_fill_keys(preg_split('/\s+/', trim(self::STOP_TEXT)), true);
        }
        return self::$stop;
    }

    // ---- words ----------------------------------------------------------------------------------------------

    public static function tokens($text)
    {
        preg_match_all('/[a-z0-9]+/', strtolower(str_replace("'", '', (string)$text)), $m);
        return $m[0];
    }

    /** The words that carry meaning, cut to a stem (laughing, laughter -> laugh). */
    public static function stems($text)
    {
        $stop = self::stop();
        $out = [];
        foreach (self::tokens($text) as $w) {
            $w = (string)$w;
            if (isset($stop[$w]) || strlen($w) < 2) {
                continue;
            }
            foreach (['ing', 'ed', 's'] as $suf) {
                if (strlen($w) > strlen($suf) + 2 && substr($w, -strlen($suf)) === $suf) {
                    $w = substr($w, 0, -strlen($suf));
                    break;
                }
            }
            $out[] = substr($w, 0, 5);
        }
        return $out;
    }

    /** Edit distance counting an adjacent swap as one (humna -> human); limit + 1 when it is more than limit. */
    public static function editDistance($a, $b, $limit)
    {
        $la = strlen($a);
        $lb = strlen($b);
        if (abs($la - $lb) > $limit) {
            return $limit + 1;
        }
        $prev2 = null;
        $prev = range(0, $lb);
        for ($i = 1; $i <= $la; $i++) {
            $cur = array_fill(0, $lb + 1, 0);
            $cur[0] = $i;
            for ($j = 1; $j <= $lb; $j++) {
                $cur[$j] = min($prev[$j] + 1, $cur[$j - 1] + 1, $prev[$j - 1] + ($a[$i - 1] !== $b[$j - 1] ? 1 : 0));
                if ($i > 1 && $j > 1 && $a[$i - 1] === $b[$j - 2] && $a[$i - 2] === $b[$j - 1]) {
                    $cur[$j] = min($cur[$j], $prev2[$j - 2] + 1);
                }
            }
            $prev2 = $prev;
            $prev = $cur;
        }
        return $prev[$lb];
    }

    /** The text with its misspelt words mended (see faq_fix in tvchat-backend). */
    public static function fix(array &$faq, $text)
    {
        $stop = self::stop();
        if (!isset($faq['_vocab'])) {
            $vocab = [];
            foreach ($faq['entries'] as $e) {
                foreach ([$e['q'], $e['a'], $e['k']] as $part) {
                    foreach (self::tokens($part) as $t) {
                        $t = (string)$t;
                        $vocab[$t] = ($vocab[$t] ?? 0) + 1;
                    }
                }
            }
            foreach ($stop as $w => $_) {
                $vocab[(string)$w] = ($vocab[(string)$w] ?? 0) + 50;      // (a filler word is the likeliest thing meant)
            }
            $faq['_vocab'] = $vocab;
        }
        $vocab = $faq['_vocab'];
        $out = [];
        foreach (self::tokens($text) as $w) {
            $w = (string)$w;
            if (isset($vocab[$w]) || strlen($w) < 4 || ctype_digit($w)) {
                $out[] = $w;
                continue;
            }
            $limit = strlen($w) < 8 ? 1 : 2;
            $best = null;
            foreach ($vocab as $v => $n) {
                $v = (string)$v;
                if (abs(strlen($v) - strlen($w)) > $limit || strlen($v) < 3 || (strlen($w) === 4 && !isset($stop[$v]))) {
                    continue;
                }
                $d = self::editDistance($w, $v, $limit);
                if ($d <= $limit && ($best === null || $d < $best[0] || ($d === $best[0] && ($n > $best[1] || ($n === $best[1] && strcmp($v, $best[2]) < 0))))) {
                    $best = [$d, $n, $v];
                }
            }
            $out[] = $best ? $best[2] : $w;
        }
        return implode(' ', $out);
    }

    // ---- the FAQ file -----------------------------------------------------------------------------------------

    public static function parseFaq($text)
    {
        $faq = ['hello' => '', 'fallback' => '', 'thanks' => '', 'warning' => '', 'goodbye' => '', 'lost' => '', 'help' => '', 'about' => '', 'entries' => []];
        $cur = null;                    // [&container, key]
        $entries = [];
        foreach (preg_split('/\r\n|\r|\n/', (string)$text) as $line) {
            $s = preg_replace('/^\s+|\s+$/u', '', $line);
            if ($s === null) {
                $s = trim($line);
            }
            if ($s !== '' && $s[0] === '#') {
                continue;
            }
            if (preg_match('/^(Q|A|K|Hello|Fallback|Thanks|Warning|Goodbye|Lost|Help|About)\s*:\s*(.*)$/', $s, $m)) {
                $tag = $m[1];
                $rest = $m[2];
                if ($tag === 'Q') {
                    $entries[] = ['q' => $rest, 'a' => '', 'k' => ''];
                    $cur = ['entry', count($entries) - 1, 'q'];
                } elseif ($tag === 'A' || $tag === 'K') {
                    if (!$entries) {
                        $cur = null;
                        continue;
                    }
                    $cur = ['entry', count($entries) - 1, strtolower($tag)];
                    $entries[count($entries) - 1][strtolower($tag)] = $rest;
                } else {
                    $cur = ['faq', strtolower($tag)];
                    $faq[strtolower($tag)] = $rest;
                }
            } elseif ($s !== '' && $cur) {
                if ($cur[0] === 'entry') {
                    $entries[$cur[1]][$cur[2]] .= "\n" . $s;
                } else {
                    $faq[$cur[1]] .= "\n" . $s;
                }
            }
        }
        $faq['entries'] = array_values(array_filter($entries, function ($e) {
            return trim($e['q']) !== '' && trim($e['a']) !== '';
        }));
        return $faq;
    }

    /** [[score, entry, cover, qfrac] ...], best first. */
    public static function rank(array &$faq, $text)
    {
        $words = array_flip(array_unique(self::stems(self::fix($faq, mb_substr((string)$text, 0, 300)))));
        $ents = $faq['entries'];
        if (!$words || !$ents) {
            return [];
        }
        if (!isset($faq['_docs'])) {
            $docs = [];
            foreach ($ents as $e) {
                $docs[] = [array_flip(self::stems($e['q'])), array_flip(self::stems($e['a'])), array_flip(self::stems($e['k']))];
            }
            $faq['_docs'] = $docs;
        }
        $docs = $faq['_docs'];
        $n = count($ents);
        $idf = [];
        foreach ($words as $w => $_) {
            $w = (string)$w;
            $df = 0;
            foreach ($docs as [$q, $a, $k]) {
                if (isset($q[$w]) || isset($a[$w]) || isset($k[$w])) {
                    $df++;
                }
            }
            $idf[$w] = log(1 + $n / max($df, 1));
        }
        $best = 3 * array_sum($idf);
        $out = [];
        foreach ($ents as $i => $e) {
            [$q, $a, $k] = $docs[$i];
            $s = 0.0;
            foreach ($idf as $w => $v) {
                $w = (string)$w;
                $s += $v * (isset($k[$w]) ? (isset($q[$w]) ? 4 : 3) : (isset($q[$w]) ? 2 : (isset($a[$w]) ? 0.5 : 0)));
            }
            $hit = 0;
            foreach ($q as $w => $_) {
                if (isset($words[$w])) {
                    $hit++;
                }
            }
            $out[] = [$s, $e, $s / $best, $hit / max(count($q), 1)];
        }
        usort($out, function ($x, $y) {                 // a tie: the entry whose question is more fully matched
            $a = -round($x[0], 6);
            $b = -round($y[0], 6);
            return $a <=> $b ?: (-$x[3] <=> -$y[3]);
        });
        return $out;
    }

    // ---- abuse ----------------------------------------------------------------------------------------------

    public static function squash($s)
    {
        return preg_replace('/(.)\1+/su', '$1', $s);
    }

    private static function lists()
    {
        if (self::$swearStems === null) {
            self::$swearStems = array_map([__CLASS__, 'squash'], ['fuck', 'fuk', 'fck', 'fsck', 'shit', 'bullshit', 'bugger']);
            self::$insultStems = array_map([__CLASS__, 'squash'], ['bitch', 'biatch', 'bastard', 'asshole', 'arsehole', 'cunt', 'wank', 'douche']);
            self::$abuseWords = array_fill_keys(array_map([__CLASS__, 'squash'], ['dick', 'dicks', 'dickhead', 'prick', 'twat', 'slut',
                'whore', 'retard', 'retarded', 'idiot', 'idiots', 'moron', 'morons', 'imbecile', 'scumbag', 'dumbass', 'jackass']), true);
        }
    }

    /** Lower case, apostrophes gone, each word without the punctuation round it, digits and symbols turned back. */
    public static function normalized($text)
    {
        $map = ['0' => 'o', '1' => 'i', '3' => 'e', '4' => 'a', '5' => 's', '7' => 't', '@' => 'a', '$' => 's', '!' => 'i', '*' => 'u'];
        $out = [];
        foreach (preg_split('/\s+/u', trim(str_replace("'", '', mb_strtolower((string)$text))), -1, PREG_SPLIT_NO_EMPTY) as $w) {
            $out[] = strtr(trim($w, "!?.,;:\"()[]{}"), $map);
        }
        return implode(' ', $out);
    }

    /** True when the text has a profane or insulting word (strict: also swearing and rude phrases). */
    public static function abusive($text, array $extra = [], $tolerant = false)
    {
        self::lists();
        $norm = self::normalized($text);
        if (preg_match('/\byou(?:re|r|\s+are|\s+r)?\s+(?:(?:such|so|really|very|just|a|an|the|complete|total|absolute)\s+)*'
            . '(?:stupid|dumb|useless|pathetic|worthless|garbage|trash|crap|rubbish|sucks?|terrible|awful|ugly|lame|loser|moron|idiot)\b/', $norm)) {
            return true;
        }
        if (!$tolerant && preg_match('/\b(?:shut up|bite me|piss off|bugger off|screw (?:you|off)|go to hell|get stuffed)\b/', $norm)) {
            return true;
        }
        $stems = $tolerant ? self::$insultStems : array_merge(self::$swearStems, self::$insultStems);
        $extra = array_fill_keys($extra, true);
        preg_match_all('/[a-z]+/', self::squash($norm), $m);
        foreach ($m[0] as $t) {
            if ($t === 'shitake') {
                continue;
            }
            if (isset(self::$abuseWords[$t]) || isset($extra[$t])) {
                return true;
            }
            foreach ($stems as $s) {
                if (strncmp($t, $s, strlen($s)) === 0) {
                    return true;
                }
            }
        }
        return false;
    }

    /** What a visitor typed, with email addresses and phone-like numbers blanked. */
    public static function redact($text)
    {
        $text = preg_replace('/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/', '<email>', (string)$text);
        return preg_replace('/\+?\d[\d\s().-]{6,}\d/', '<number>', $text);
    }

    // ---- what she says ----------------------------------------------------------------------------------------

    /**
     * One visitor line in, what to do out:  ['say' => [text, ...], 'note' => [kind, entry, score, cover] | null,
     * 'end' => null | 'abuse' | 'lost'].  $st is the chat's state (greeted, misses, strikes, muted), kept by the caller.
     * $opt: max_misses (default 5), abuse_extra (more words), abuse (default true).
     */
    public static function reply(array &$faq, array &$st, $text, array $opt = [])
    {
        $st += ['greeted' => false, 'misses' => 0, 'strikes' => 0, 'muted' => false];
        $r = ['say' => [], 'note' => null, 'end' => null];
        if ($st['muted']) {
            return $r;
        }
        if (($opt['abuse'] ?? true) && self::abusive($text, $opt['abuse_extra'] ?? [])) {     // strict: flagged at once, ended the second time
            $st['strikes']++;
            if ($st['strikes'] < 2) {
                $r['say'][] = $faq['warning'] ?: "I'm happy to help, but I can't continue if the language isn't respectful. Please keep it friendly.";
                $r['note'] = ['abuse', '', 0, 0];
                return $r;
            }
            $st['muted'] = true;
            $r['say'][] = $faq['goodbye'] ?: "I'm ending this chat because the language has not been respectful. A person can help: please contact us directly.";
            $r['note'] = ['kick', '', 0, 0];
            $r['end'] = 'abuse';
            return $r;
        }
        $hello = $faq['hello'] ?: "Hello! Ask me a question and I'll look it up.";
        $first = !$st['greeted'];
        $st['greeted'] = true;
        $low = trim(mb_strtolower((string)$text));
        $ok = function ($kind) use (&$st) {
            $st['misses'] = 0;
            return $kind;
        };
        if (preg_match('/^!joke\s*$/', $low)) {
            $j = JoyJokes::LIST[random_int(0, count(JoyJokes::LIST) - 1)];
            $r['say'] = [$j[0], preg_split('/(?<=[.!?])\s+/', $j[1], 2)[0]];
            $r['note'] = [$ok('joke'), '', 0, 0];
        } elseif (preg_match('/^(?:hi|hello|hey|hiya|howdy|good (?:morning|afternoon|evening))\b[\s\W]*(?:there|floyd)?\W*$/', $low)) {
            $r['say'][] = $hello;
            $r['note'] = [$ok('hello'), '', 0, 0];
        } elseif (preg_match('/^(?:thanks|thank you|thx|cheers|ok thanks|great thanks)\b/', $low)) {
            $r['say'][] = $faq['thanks'] ?: "You're welcome!";
            $r['note'] = [$ok('thanks'), '', 0, 0];
        } elseif (preg_match('/^(?:[!\/]\s*(?:help|commands?|menu|h|\?)|help|\?+|commands?|menu|options|what can you (?:do|help with)|'
            . 'how does this work|how do i use (?:this|you))\W*$/', $low)) {
            $r['say'][] = $faq['help'] ?: self::HELP_TEXT;
            $r['note'] = [$ok('command'), '', 0, 0];
        } elseif (preg_match('/^(?:[!\/]\s*(?:about|who|whoami|bot|status|version|info|start|hello|hi)|(?:who|what)\s+(?:are|r)\s+(?:you|u)'
            . '(?:\s+exactly)?|who\s+is\s+this|what\s+is\s+this|who\s+am\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)|'
            . '(?:what(?:\'s|s|\s+is)\s+)?your\s+name|introduce\s+yourself|tell\s+me\s+about\s+yourself|what\s+do\s+you\s+do)\W*$/', $low)) {
            $r['say'][] = $faq['about'] ?: $hello;
            $r['note'] = [$ok('command'), '', 0, 0];
        } elseif (!preg_match('/[A-Za-z0-9]/', (string)$text)) {         // emoji and symbols only: a smile is not a miss
            $r['say'][] = "Thank you! I can only read words, I'm afraid, but I appreciate the smile. How can I help?";
            $r['note'] = ['other', '', 0, 0];
        } else {
            $command = in_array(substr($low, 0, 1), ['!', '/'], true);   // a command she does not know: not a FAQ question
            $ranked = $command ? [] : self::rank($faq, $text);
            if ($first) {
                $r['say'][] = $hello;
            }
            if ($ranked && $ranked[0][0] >= self::FAQ_MIN && $ranked[0][2] >= self::FAQ_COVER) {
                $answer = $ranked[0][1]['a'];
                if (count($ranked) > 1 && $ranked[1][0] >= 0.8 * $ranked[0][0] && $ranked[1][0] >= self::FAQ_MIN) {
                    $answer .= "\n(If you meant \"" . trim(explode('?', $ranked[1][1]['q'])[0]) . "\" just ask me.)";
                }
                $r['say'][] = $answer;
                $r['note'] = ['answer', trim(explode('?', $ranked[0][1]['q'])[0]), $ranked[0][0], $ranked[0][2]];
                $st['misses'] = 0;
            } else {
                $best = $ranked ? $ranked[0] : [0.0, ['q' => ''], 0.0];
                $note = [trim(explode('?', $best[1]['q'])[0]), $best[0], $best[2]];
                $n = ++$st['misses'];
                $max = (int)($opt['max_misses'] ?? 5);
                if ($max && $n > $max) {                                  // lost, or probing: say so, and end the chat (no ban)
                    $st['muted'] = true;
                    $r['say'] = [$faq['lost'] ?: "You seem to be having an issue getting an answer from me, and I'm sorry about that. I'll end this chat now. A person can help: please contact us directly."];
                    $r['note'] = ['lost', $note[0], $note[1], $note[2]];
                    $r['end'] = 'lost';
                    return $r;
                }
                $first_word = $command ? mb_substr(preg_split('/\s+/u', trim((string)$text))[0], 0, 20) : '';
                $r['say'][] = $command
                    ? "I don't use commands like $first_word - just ask me a question in plain words, for example \"What should I wear?\" or \"How much does a session cost?\""
                    : ($faq['fallback'] ?: "Sorry, I don't have an answer to that one. A person can help.");
                $r['note'] = ['miss', $note[0], $note[1], $note[2]];
            }
        }
        return $r;
    }
}
