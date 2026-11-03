<?php
/**
 * Holds the PHP Joy (JoyBrain) to the Python Floyd-pro: tests/golden.json is what the Python original said, step by step,
 * to a set of conversations (see gen_joy_golden.py); here every step must give the same words, and end the chat at the same
 * step.  Usage:  php tests/joy_golden.php [faq.txt] [golden.json]       exit status 1 on any mismatch.
 */
require __DIR__ . '/../extension/joy/classes/JoyBrain.php';
require __DIR__ . '/../extension/joy/classes/JoyJokes.php';

$faqFile = isset($argv[1]) ? $argv[1] : __DIR__ . '/../../configure/faq.example.txt';
$goldenFile = isset($argv[2]) ? $argv[2] : __DIR__ . '/golden.json';
$faq = JoyBrain::parseFaq(file_get_contents($faqFile));
$data = json_decode(file_get_contents($goldenFile), true);
$steps = 0;
$bad = 0;
foreach ($data as $sc) {
    $st = array();
    $closed = false;
    foreach ($sc['steps'] as $step) {
        $steps++;
        if (trim($step['in']) === '') {                       // the extension never passes a blank line on (the reference said nothing)
            if ($step['out'] !== array()) { $bad++; echo "MISMATCH: a blank line was answered\n"; }
            continue;
        }
        $r = JoyBrain::reply($faq, $st, $step['in'], array('max_misses' => 5));
        $closed = $closed || $r['end'] !== null;
        if (trim(mb_strtolower($step['in'])) === '!joke') {                // random: check what a joke is, not which
            $found = false;
            foreach (JoyJokes::LIST as $j) {
                if (count($r['say']) === 2 && $r['say'][0] === $j[0] && $r['say'][1] === preg_split('/(?<=[.!?])\s+/', $j[1], 2)[0]) {
                    $found = true;
                }
            }
            $ok = $found && count($step['out']) === 2;
        } else {
            $lines = array();                                  // the reference sends each LINE of an answer as a bubble, PHP the answer
            foreach ($r['say'] as $text) {                     // as one bubble with line breaks: compare line by line
                foreach (explode("\n", $text) as $l) { if (trim($l) !== '') { $lines[] = $l; } }
            }
            $ok = $lines === array_values(array_filter($step['out'], function ($l) { return trim($l) !== ''; })) && $closed === $step['closed'];
        }
        if (!$ok) {
            $bad++;
            echo "MISMATCH in '{$sc['name']}' at: " . json_encode($step['in'], JSON_UNESCAPED_UNICODE) . "\n";
            echo "  python: " . json_encode($step['out'], JSON_UNESCAPED_UNICODE) . ' closed=' . var_export($step['closed'], true) . "\n";
            echo "  php:    " . json_encode($r['say'], JSON_UNESCAPED_UNICODE) . ' closed=' . var_export($closed, true) . "\n";
        }
    }
}
echo "$steps steps in " . count($data) . " conversations, $bad mismatches\n";
exit($bad ? 1 : 0);
