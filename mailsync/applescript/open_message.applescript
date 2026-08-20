-- Open one message in Mail, given its RFC-822 Message-ID.
--
-- Values arrive via argv; nothing is interpolated into this script.
-- argv:  1 accountAddress   2 mailboxName   3 rfc822MessageId

on run argv
	set acctAddr to item 1 of argv
	set mboxName to item 2 of argv
	set wantedId to item 3 of argv

	tell application "Mail"
		activate
		repeat with acct in accounts
			if (email addresses of acct) contains acctAddr then
				repeat with mbox in (every mailbox of acct)
					if name of mbox is mboxName then
						set hits to (messages of mbox whose message id is wantedId)
						if (count of hits) > 0 then
							open (item 1 of hits)
							return "ok"
						end if
					end if
				end repeat
			end if
		end repeat
	end tell
	return "notfound"
end run
