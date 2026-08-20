-- Create an outgoing message and either send it or leave it in Drafts.
--
-- Every value arrives through argv, so nothing is ever interpolated into this
-- script's text and no escaping is required or attempted.
--
-- argv:
--   1  mode        "send" | "draft"
--   2  sender      full "Name <addr>" or bare address; selects the account
--   3  subject
--   4  body
--   5  visible     "yes" | "no"
--   6  toCount
--   7  ccCount
--   8  bccCount
--   9  attachCount
--   then toCount + ccCount + bccCount addresses, then attachCount POSIX paths

on run argv
	set theMode to item 1 of argv
	set theSender to item 2 of argv
	set theSubject to item 3 of argv
	set theBody to item 4 of argv
	set showIt to (item 5 of argv is "yes")
	set nTo to (item 6 of argv) as integer
	set nCc to (item 7 of argv) as integer
	set nBcc to (item 8 of argv) as integer
	set nAtt to (item 9 of argv) as integer

	set cursor to 10

	tell application "Mail"
		set newMessage to make new outgoing message with properties {subject:theSubject, content:theBody, visible:showIt}
		tell newMessage
			set sender to theSender

			repeat with i from 1 to nTo
				make new to recipient at end of to recipients with properties {address:(item cursor of argv)}
				set cursor to cursor + 1
			end repeat
			repeat with i from 1 to nCc
				make new cc recipient at end of cc recipients with properties {address:(item cursor of argv)}
				set cursor to cursor + 1
			end repeat
			repeat with i from 1 to nBcc
				make new bcc recipient at end of bcc recipients with properties {address:(item cursor of argv)}
				set cursor to cursor + 1
			end repeat

			repeat with i from 1 to nAtt
				set attPath to (item cursor of argv)
				tell content
					make new attachment with properties {file name:(POSIX file attPath as alias)} at after last paragraph
				end tell
				set cursor to cursor + 1
			end repeat
		end tell

		if theMode is "send" then
			send newMessage
			return "sent"
		else
			save newMessage
			return "drafted"
		end if
	end tell
end run
