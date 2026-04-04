package chooser

func ChooseBucket(flag bool) string {
	if flag {
		return "enabled"
	}
	return "disabled"
}
