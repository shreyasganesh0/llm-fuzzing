TEST(Regexp, CaptureNames) {
  Regexp* x;
  RegexpStatus status;
  x = Regexp::Parse(
      "(?P<g1>a+)|(e)(?P<g2>w*)+(?P<g1>b+)", Regexp::PerlX, &status);
  EXPECT_TRUE(status.ok());
  EXPECT_EQ(4, x->NumCaptures());
  const map<int, string>* have = x->CaptureNames();
  EXPECT_TRUE(have != NULL);
  EXPECT_EQ(3, have->size());
  map<int, string> want;
  want[1] = "g1";
  want[3] = "g2";
  want[4] = "g1";

  EXPECT_EQ(want, *have);
  x->Decref();
  delete have;
}