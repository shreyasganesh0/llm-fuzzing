// Test that overflowed ref counts work.
TEST(Regexp, BigRef) {
  Regexp* re;
  re = Regexp::Parse("x", Regexp::NoParseFlags, NULL);
  for (int i = 0; i < 100000; i++)
    re->Incref();
  for (int i = 0; i < 100000; i++)
    re->Decref();
  CHECK_EQ(re->Ref(), 1);
  re->Decref();
}