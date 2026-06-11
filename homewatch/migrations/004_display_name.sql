-- A user-set display name for a device, kept separate from the detected name
-- (mDNS/AirPlay) so re-detection never clobbers what you chose. See spec §13.
ALTER TABLE devices ADD COLUMN display_name TEXT;
