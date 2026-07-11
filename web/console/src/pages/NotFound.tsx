import { Link } from "react-router";
import { Anchor, Paper, Stack, Text, Title } from "@mantine/core";

export function NotFound() {
  return (
    <Paper withBorder maw={448} mx="auto" mt={96} p="xl" ta="center">
      <Stack gap="sm">
        <Title order={1} size="h4">Nothing here</Title>
        <Text size="sm" c="dimmed">This route doesn&apos;t exist.</Text>
        <Anchor component={Link} to="/" size="sm" mt="xs">
          Back to the Fleet Board
        </Anchor>
      </Stack>
    </Paper>
  );
}
