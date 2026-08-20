-- Return the full raw source of one message, forcing Mail to download it if the
-- local copy is header-only.
--
-- argv:  1 accountAddress   2 mailboxName   3 messageId (RFC-822 Message-ID)

on run argv
	set acctAddr to item 1 of argv
	set mboxName to item 2 of argv
	set wantedId to item 3 of argv

	tell application "Mail"
		repeat with acct in accounts
			if (email addresses of acct) contains acctAddr then
				repeat with mbox in mailboxes of acct
					if name of mbox is mboxName then
						repeat with msg in (messages of mbox whose message id is wantedId)
							return source of msg
						end repeat
					end if
				end repeat
			end if
		end repeat
	end tell
	return ""
end run
