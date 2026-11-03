<?php
/**
 * Joy's settings.  The deploy script (tvchat/deploy/joy-webhost-on-whatwelove.sh) writes the real
 * extension/joy/settings/settings.ini.php from this (nothing in it is secret).
 */
return array(
    'enabled'       => true,
    'dep_id'        => 2,                                          // the department Joy answers in ("assist")
    'operator_id'   => 2,                                          // her operator (user) number: the name her lines carry, who closes chats
    'operator_name' => 'Joy, Universal Laughter assistant',
    'faq'           => '/home/whatwelo/joy/faq.txt',               // her FAQ, outside the web root; she reads it again each time
    'log'           => '/home/whatwelo/joy/questions.log',         // what visitors asked (emails and numbers blanked)
    'abuse_words'   => '',                                         // optional: a file of more words, one per line
    'max_misses'    => 5,                                          // more answers missed in a row than this: she says goodbye (0 = never)
    'ban_minutes'   => 60,                                         // an abuser's address is turned away this long (0 = this chat only)
    'kill_file'     => '/home/whatwelo/joy/DISABLED',              // while this file exists she says nothing
    // Urgent mail to a person, at once (empty alert_to = off): her own code failing, or a visitor using one of the alert words.
    'alert_to'      => 'chris@universal-laughter.com',
    'alert_from'    => 'joy@universal-laughter.com',
    'alert_state'   => '/home/whatwelo/joy/alerts.json',           // throttle: the same alert once an hour, ten mails an hour at most
    'alert_words'   => array('urgent', 'emergency', 'asap', 'complaint', 'refund', 'lawyer'),
    'admin_url'     => 'https://chat.universal-laughter.com/index.php/site_admin/chat/single/',
);
