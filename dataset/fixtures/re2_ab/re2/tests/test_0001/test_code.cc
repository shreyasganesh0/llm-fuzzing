TEST(Regexp, NamedCaptures) {
  Regexp* x;
  RegexpStatus status;
  x = Regexp::Parse(
      "(?P<g1>a+)|(e)(?P<g2>w*)+(?P<g1>b+)", Regexp::PerlX, &status);
  EXPECT_TRUE(status.ok());
  EXPECT_EQ(4, x->NumCaptures());
  const map<string, int>* have = x->NamedCaptures();
  EXPECT_TRUE(have != NULL);
  EXPECT_EQ(2, have->size());
  map<string, int> want;
  want["g1"] = 1;
  want["g2"] = 3;
  EXPECT_EQ(want, *have);
  x->Decref();
  delete have;
}