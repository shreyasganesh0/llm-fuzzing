// Synthetic Google Test fixture used by extractor unit tests.
// This is NOT derived from any real project; it exists solely to drive
// phase1_dataset/scripts/extractors/googletest.py test coverage.

#include "gtest/gtest.h"

namespace example {

TEST(RE2, FullMatch) {
  ASSERT_TRUE(RE2::FullMatch("hello", "h.*o"));
  EXPECT_EQ(1, 1);
}

TEST(RE2, PartialMatch) {
  std::string out;
  EXPECT_TRUE(RE2::PartialMatch("greetings", "(gr.+t)", &out));
  EXPECT_EQ(out, "greet");
}

TEST_F(RegexpTest, Parse) {
  ASSERT_TRUE(Parse("abc"));
}

}  // namespace example
